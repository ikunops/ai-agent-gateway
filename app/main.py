import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from app.api.chat import router as chat_router
from app.api.refine import router as refine_router
from app.core.config import Config, load_config, upstream_api_key
from app.layers.cache import CacheEngine
from app.layers.local_picker import LocalDigestPicker
from app.layers.registry import ProjectRegistry
from app.layers.response_cache import ResponseCache, stats_snapshot
from app.layers.router import SemanticRouter
from app.layers.routing_log import RoutingLedger
from app.layers.stats import Stats
from app.upstream.classifier import ClassifierClient, ClassifierEnsemble


class ProjectBody(BaseModel):
    project_id: str
    agents_path: str
    description: str = ""


def _build_classifier(config: Config):
    """分类器装配：用户自配 gateway.classifiers（多分类器交叉验证）优先；
    否则回退 upstream.default 单分类器；都没有则 None（纯本地规则路由）。"""
    clients = []
    for c in config.classifiers:
        base = str(c.get("base_url", "")).strip()
        if not base:
            continue
        clients.append(ClassifierClient(
            name=str(c.get("name", base)),
            base_url=base,
            api_key=os.environ.get(str(c.get("api_key_env", "")), ""),
            model=str(c.get("model", "qwen2.5:7b")),
            weight=float(c.get("weight", 1.0)),
        ))
    if clients:
        return ClassifierEnsemble(clients) if len(clients) > 1 else clients[0]
    if config.upstream.base_url:
        return ClassifierClient(
            "default", config.upstream.base_url,
            upstream_api_key(config) or "", config.upstream.default_model,
        )
    return None


def create_app(config: Config) -> FastAPI:
    app = FastAPI(title="AI Gateway", version="0.3.0")
    app.state.config = config
    app.state.stats = Stats(config.audit_dir, config.audit_keep_days)
    app.state.cache = CacheEngine(project_root=None, max_sessions=config.tier3_max_sessions)
    app.state.registry = ProjectRegistry(str(Path(config.data_dir) / "projects.json"))
    app.state.cache.bind_registry(app.state.registry)
    app.state.cache.set_route_profiles(config.routes)
    app.state.resp_cache = ResponseCache(str(Path(config.data_dir) / "resp_cache"))
    picker = None
    if config.digest_picker_enabled and config.digest_picker_url:
        picker = LocalDigestPicker(
            config.digest_picker_url,
            config.digest_picker_model or "qwen2.5:7b",
            config.digest_picker_max_segments,
        )
    app.state.router = SemanticRouter(
        config.routes,
        threshold=config.router_threshold,
        cache_ttl=config.router_cache_ttl,
        picker=picker,
    )
    app.state.llm_router = _build_classifier(config)
    app.state.routing_ledger = RoutingLedger(
        str(Path(config.audit_dir) / "routing"), config.audit_keep_days
    )
    app.include_router(chat_router)
    app.include_router(refine_router)

    @app.get("/v1/health")
    async def health():
        return {"status": "ok"}

    @app.get("/v1/stats/hits")
    async def stats_hits(request: Request):
        return {
            "exact_cache": stats_snapshot(),
            "prefix": request.app.state.stats.snapshot(),
        }

    @app.get("/v1/stats/routing")
    async def stats_routing(request: Request):
        """路由决策明细账摘要：来源/标签/Tier 分布 + 高频技术词 + 平均延迟，供调优。"""
        return request.app.state.routing_ledger.summarize()

    @app.post("/v1/projects")
    async def project_register(body: ProjectBody, request: Request):
        registry: ProjectRegistry = request.app.state.registry
        return registry.register(body.project_id, body.agents_path, body.description)

    @app.delete("/v1/projects/{project_id}")
    async def project_delete(project_id: str, request: Request):
        registry: ProjectRegistry = request.app.state.registry
        if not registry.unregister(project_id):
            raise HTTPException(status_code=404, detail="project not found")
        return {"deleted": project_id}

    @app.get("/v1/projects")
    async def project_list(request: Request):
        return {"projects": request.app.state.registry.all()}

    @app.post("/v1/cache/clear")
    async def cache_clear(request: Request):
        cleared = request.app.state.resp_cache.clear()
        return {"cleared": cleared}

    @app.get("/v1/models")
    async def models(request: Request):
        cfg: Config = request.app.state.config
        if not cfg.upstream.base_url:
            return {"object": "list", "data": []}
        return {
            "object": "list",
            "data": [{"id": cfg.upstream.default_model, "object": "model"}],
        }

    return app


app = create_app(load_config())
