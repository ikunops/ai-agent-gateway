import os
from pathlib import Path
from typing import Any, Dict

import yaml
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent.parent


class UpstreamConfig(BaseModel):
    base_url: str
    api_key_env: str
    default_model: str = "deepseek-chat"


class Config(BaseModel):
    server_host: str = "127.0.0.1"
    server_port: int = 8080
    api_keys: Dict[str, str] = {"default": "gateway-dev-key"}
    upstream: UpstreamConfig
    anchor_prompt: str = ""
    project_agents: Dict[str, str] = {}
    routes: Dict[str, str] = {}
    router_threshold: float = 0.55
    router_cache_ttl: int = 3600
    tier3_max_sessions: int = 200
    audit_dir: str = "logs"
    audit_keep_days: int = 30
    data_dir: str = "data"


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
            base_url=upstream_raw.get("base_url", "https://api.deepseek.com/v1"),
            api_key_env=api_key_env,
            default_model=upstream_raw.get("default_model", "deepseek-chat"),
        ),
        anchor_prompt=_deep_get(raw, "gateway.anchor_prompt", ""),
        project_agents=_deep_get(raw, "gateway.project_agents", {}),
        routes=_flatten_routes(_deep_get(raw, "gateway.routes", {})),
        router_threshold=float(_deep_get(raw, "gateway.router.vector_threshold", 0.55)),
        router_cache_ttl=int(_deep_get(raw, "gateway.router.cache_ttl_seconds", 3600)),
        tier3_max_sessions=int(_deep_get(raw, "cache.tier3_max_sessions", 200)),
        audit_dir=_deep_get(raw, "audit.dir", "logs"),
        audit_keep_days=int(_deep_get(raw, "audit.keep_days", 30)),
        data_dir=_deep_get(raw, "data.dir", "data"),
    )


def upstream_api_key(config: Config) -> str:
    return os.environ.get(config.upstream.api_key_env, "")
