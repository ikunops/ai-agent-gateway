from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from app.api.chat import router as chat_router
from app.core.config import Config, load_config
from app.layers.cache import CacheEngine
from app.layers.registry import ProjectRegistry
from app.layers.response_cache import ResponseCache, stats_snapshot
from app.layers.stats import Stats


class ProjectBody(BaseModel):
    project_id: str
    agents_path: str
    description: str = ""


def create_app(config: Config) -> FastAPI:
    app = FastAPI(title="AI Gateway", version="0.2.0")
    app.state.config = config
    app.state.stats = Stats(config.audit_dir, config.audit_keep_days)
    app.state.cache = CacheEngine(project_root=None, max_sessions=config.tier3_max_sessions)
    app.state.registry = ProjectRegistry(str(Path(config.data_dir) / "projects.json"))
    app.state.cache.bind_registry(app.state.registry)
    app.state.resp_cache = ResponseCache(str(Path(config.data_dir) / "resp_cache"))
    app.include_router(chat_router)

    @app.get("/v1/health")
    async def health():
        return {"status": "ok"}

    @app.get("/v1/stats/hits")
    async def stats_hits(request: Request):
        return {
            "exact_cache": stats_snapshot(),
            "prefix": request.app.state.stats.snapshot(),
        }

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
        return {
            "object": "list",
            "data": [{"id": cfg.upstream.default_model, "object": "model"}],
        }

    return app


app = create_app(load_config())
