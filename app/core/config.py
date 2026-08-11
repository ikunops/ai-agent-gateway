import os
from pathlib import Path
from typing import Any, Dict, List

import yaml
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent.parent

DEFAULT_CLARIFY_PROMPT = (
    "[需求澄清模式]\n"
    "用户提出了开放式开发需求，但未指定平台和技术栈。\n"
    "本次回复请先输出四个精准的澄清问题，不要直接给出实现方案：\n"
    "1. 目标平台（Android/iOS/Web/桌面）？\n"
    "2. 核心痛点（缓存清理/卸载残留/重复文件/隐私清理）？\n"
    "3. 目标用户（个人使用/企业分发/公开上架）？\n"
    "4. 现有代码库还是从零开始？"
)


class UpstreamConfig(BaseModel):
    """上游模型端点。网关默认是纯整形模式（/v1/refine，零上游依赖）——
    不持有模型 key、不选模型；base_url 留空即纯整形，转发端点自动禁用。"""

    base_url: str = ""
    api_key_env: str = "DEEPSEEK_API_KEY"
    default_model: str = "deepseek-chat"


class Config(BaseModel):
    server_host: str = "127.0.0.1"
    server_port: int = 8080
    api_keys: Dict[str, str] = {"default": "gateway-dev-key"}
    upstream: UpstreamConfig
    anchor_prompt: str = ""
    clarify_prompt: str = DEFAULT_CLARIFY_PROMPT
    project_agents: Dict[str, str] = {}
    routes: Dict[str, str] = {}
    classifiers: List[Dict] = []
    router_threshold: float = 0.55
    router_cache_ttl: int = 3600
    digest_picker_enabled: bool = False
    digest_picker_url: str = ""
    digest_picker_model: str = ""
    digest_picker_max_segments: int = 4
    tier3_max_sessions: int = 200
    audit_dir: str = "logs"
    audit_keep_days: int = 30
    data_dir: str = "data"
    permission_level: str = "L1"


def _deep_get(data: Dict[str, Any], path: str, default: Any = None) -> Any:
    cur: Any = data
    for key in path.split("."):
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def _flatten_routes(raw_routes: Any) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not isinstance(raw_routes, dict):
        return out
    for name, val in raw_routes.items():
        if isinstance(val, dict):
            desc = val.get("description", "")
        elif isinstance(val, str):
            desc = val
        else:
            desc = ""
        if desc:
            out[name] = desc
    return out


def load_config(path: Path = ROOT / "config.yaml") -> Config:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    upstream_raw = _deep_get(raw, "upstream.default", {}) or {}
    api_key_env = upstream_raw.get("api_key_env", "DEEPSEEK_API_KEY")

    return Config(
        server_host=_deep_get(raw, "server.host", "127.0.0.1"),
        server_port=int(_deep_get(raw, "server.port", 8080)),
        api_keys=_deep_get(raw, "auth.api_keys", {}),
        upstream=UpstreamConfig(
            base_url=upstream_raw.get("base_url", ""),
            api_key_env=api_key_env,
            default_model=upstream_raw.get("default_model", "deepseek-chat"),
        ),
        anchor_prompt=_deep_get(raw, "gateway.anchor_prompt", ""),
        clarify_prompt=_deep_get(raw, "gateway.clarify_prompt", DEFAULT_CLARIFY_PROMPT),
        project_agents=_deep_get(raw, "gateway.project_agents", {}),
        classifiers=[
            c for c in _deep_get(raw, "gateway.classifiers", []) or []
            if isinstance(c, dict)
        ],
        routes=_flatten_routes(_deep_get(raw, "gateway.routes", {})),
        router_threshold=float(_deep_get(raw, "gateway.router.vector_threshold", 0.55)),
        router_cache_ttl=int(_deep_get(raw, "gateway.router.cache_ttl_seconds", 3600)),
        digest_picker_enabled=bool(_deep_get(raw, "digest.local_picker.enabled", False)),
        digest_picker_url=_deep_get(raw, "digest.local_picker.base_url", ""),
        digest_picker_model=_deep_get(raw, "digest.local_picker.model", ""),
        digest_picker_max_segments=int(_deep_get(raw, "digest.local_picker.max_segments", 4)),
        tier3_max_sessions=int(_deep_get(raw, "cache.tier3_max_sessions", 200)),
        audit_dir=_deep_get(raw, "audit.dir", "logs"),
        audit_keep_days=int(_deep_get(raw, "audit.keep_days", 30)),
        data_dir=_deep_get(raw, "data.dir", "data"),
        permission_level=str(_deep_get(raw, "security.default_level", "L1")).upper(),
    )


def upstream_api_key(config: Config) -> str:
    return os.environ.get(config.upstream.api_key_env, "")
