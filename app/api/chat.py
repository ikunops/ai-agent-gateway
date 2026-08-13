import json
import os
import time
import uuid
from typing import Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.config import Config
from app.layers.cache import CacheEngine
from app.layers.permissions import check_permission
from app.layers.pipeline import process_request
from app.layers.preprocess import auth_ok, clean_messages, parse_session
from app.layers.response_cache import (
    ResponseCache,
    build_sse_chunks,
    make_session_summary,
    request_cache_key,
    stats_hit,
    stats_miss,
    stats_snapshot,
)
from app.layers.stats import Stats
from app.upstream.llm import UpstreamClient

router = APIRouter()


def _services(request: Request):
    return (
        request.app.state.config,
        request.app.state.stats,
        request.app.state.cache,
        request.app.state.resp_cache,
        request.app.state.router,
        request.app.state.llm_router,
    )


def _sse_packet(data: Dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _audit_record(request_id, session, tier, overlap, extra) -> Dict:
    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "request_id": request_id,
        "user": session.user_id,
        "project_id": session.project_id,
        "session_id": session.session_id,
        "tier": tier,
        "prefix_overlap": overlap,
        "route": extra.pop("route", None) if isinstance(extra, dict) else None,
        "extra": extra,
    }


def _usage_tokens(data: Dict) -> int:
    usage = data.get("usage") or {}
    return int(usage.get("total_tokens") or 0)


@router.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    authorization: Optional[str] = Header(default=None),
):
    config, stats, cache, resp_cache, router, llm_router = _services(request)

    has_upstream = bool(config.upstream.base_url) or any(
        r.base_url for r in config.upstream_routes
    )
    if not has_upstream:
        raise HTTPException(
            status_code=400,
            detail="网关为纯整形模式（未配置 upstream.base_url / upstream.routes）：请使用 POST /v1/refine "
                   "获取增强后的请求，由 Agent 侧转发给用户当前选择的模型",
        )

    key = x_api_key
    if not key and authorization and authorization.lower().startswith("bearer "):
        key = authorization[len("bearer ") :]
    if not auth_ok(key or "", config.api_keys):
        raise HTTPException(status_code=401, detail="invalid api key")

    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="empty body")
    payload_in = json.loads(body)
    messages: list = payload_in.get("messages", [])
    stream: bool = bool(payload_in.get("stream", False))
    model: str = payload_in.get("model", "")
    normalized = model.split("/")[-1] if "/" in model else model

    route = config.resolve_upstream(normalized)
    if not route.base_url:
        raise HTTPException(
            status_code=400,
            detail=f"模型 {model!r} 未匹配任何上游路由，且未配置默认 upstream",
        )
    model = normalized or route.default_model

    session = parse_session(dict(request.headers), messages)
    cleaned = clean_messages(messages)
    request_id = f"req_{uuid.uuid4().hex[:12]}"
    upstream = UpstreamClient(route.base_url, os.environ.get(route.api_key_env, "") or "sk-none")

    exact_key = request_cache_key(body)
    cached = resp_cache.get(exact_key)
    if cached:
        cached_perm = check_permission(
            " ".join(
                m.get("content", "")
                for m in messages
                if m.get("role") == "user" and isinstance(m.get("content"), str)
            ),
            request.headers.get("x-permission-level", "").strip().upper() or config.permission_level,
        )
        if cached_perm["action"] != "block":
            stats_hit(cached)
            stats.audit.log(_audit_record(request_id, session, None, 0, {"cache": "exact-hit"}))
            resp_headers = {"X-Gateway-Cache": "HIT", "X-Gateway-Project-Id": session.project_id}
            if stream:
                async def _cached_stream():
                    for c in build_sse_chunks(cached):
                        yield _sse_packet(c)
                    yield "data: [DONE]\n\n"
                return StreamingResponse(
                    _cached_stream(),
                    media_type="text/event-stream",
                    headers=resp_headers,
                )
            return JSONResponse(content=cached, headers=resp_headers)

    stats_miss()

    result = await process_request(
        cleaned=cleaned,
        session=session,
        model=model,
        config=config,
        cache=cache,
        router=router,
        llm_router=llm_router,
        stats=stats,
        routing_ledger=request.app.state.routing_ledger,
        request_id=request_id,
    )
    final_messages = result["messages"]
    clarify_mode = result["clarify"]
    route_name, route_score, route_source = (
        result["route_name"], result["route_score"], result["route_source"]
    )
    route_meta = result["route_meta"]
    tier = result["tier"]
    overlap = result["overlap"]
    permission = result["permission"]
    if x_perm := request.headers.get("x-permission-level", "").strip().upper():
        permission = check_permission(result["user_text"], x_perm)

    if permission["action"] == "block":
        hint = ("[权限拦截] 用户请求含高危指令（"
                + "、".join(m["label"] for m in permission["matched"])
                + "），当前权限等级 " + permission["level"]
                + " 不允许执行。请解释风险并引导用户升级权限或改用低危方案，不要执行。")
        final_messages = [{"role": "system", "content": hint}] + final_messages
    perm_header = {
        "X-Gateway-Permission": permission["action"],
        "X-Gateway-Permission-Level": permission["level"],
    }

    payload_out = {
        k: v
        for k, v in payload_in.items()
        if k not in ("model", "messages", "stream")
    }
    payload_out["model"] = model
    payload_out["messages"] = final_messages
    if stream:
        payload_out["stream"] = True

    if not stream:
        async def _non_stream():
            async for item in upstream.chat_completions(payload_out, stream=False):
                if item["type"] == "error":
                    raise HTTPException(status_code=item["status"], detail=item["body"])
                data = item["data"]
                stats.record_completion(request_id, _usage_tokens(data), _now_ms())
                stats.record_route(request_id, session.project_id, session.session_id, tier)
                if permission["action"] != "block":
                    resp_cache.set(exact_key, data)
                    cache.remember(
                        session.project_id,
                        session.session_id,
                        make_session_summary(cleaned, _reply_text(data)),
                    )
                stats.audit.log(_audit_record(
                    request_id, session, tier, overlap,
                    {"cache": "miss", "clarify": clarify_mode,
                     "route": {"name": route_name, "source": route_source, "score": route_score}},
                ))
                return JSONResponse(
                    content=data,
                    headers={"X-Gateway-Cache": "MISS", "X-Gateway-Project-Id": session.project_id,
                             **perm_header},
                )

        return await _non_stream()

    async def _stream():
        yield _sse_packet({"id": request_id, "object": "chat.completion.chunk",
                           "created": int(time.time()), "model": model,
                           "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""},
                                        "finish_reason": None}]})
        content = ""
        usage = None
        finish_reason = None
        tool_calls: Dict[int, Dict] = {}
        upstream_failed = False
        async for item in upstream.chat_completions(payload_out, stream=True):
            if item["type"] == "error":
                upstream_failed = True
                yield _sse_packet({"error": {"message": item["body"], "type": "upstream_error",
                                             "code": item["status"]}})
                continue
            if item["type"] == "chunk":
                chunk = item["data"]
                if isinstance(chunk.get("usage"), dict):
                    usage = chunk["usage"]
                for ch in chunk.get("choices", []):
                    delta = ch.get("delta", {})
                    content += delta.get("content") or ""
                    if ch.get("finish_reason"):
                        finish_reason = ch["finish_reason"]
                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index", 0)
                        acc = tool_calls.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                        if tc.get("id"):
                            acc["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        acc["name"] += fn.get("name") or ""
                        acc["arguments"] += fn.get("arguments") or ""
                yield _sse_packet(chunk)
            elif item["type"] == "done":
                yield "data: [DONE]\n\n"
        if upstream_failed:
            yield "data: [DONE]\n\n"
            stats.audit.log(_audit_record(
                request_id, session, tier, overlap,
                {"cache": "miss-stream-upstream-error", "clarify": clarify_mode,
                 "route": {"name": route_name, "source": route_source, "score": route_score}},
            ))
            return
        stats.record_route(request_id, session.project_id, session.session_id, tier)
        if permission["action"] != "block" and (content or usage or tool_calls):
            message: Dict = {"role": "assistant", "content": content}
            if tool_calls:
                message["tool_calls"] = [
                    {
                        "id": v["id"] or f"call_{i}",
                        "type": "function",
                        "function": {"name": v["name"], "arguments": v["arguments"]},
                    }
                    for i, v in sorted(tool_calls.items())
                ]
            cached_resp = {
                "id": request_id,
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "message": message,
                             "finish_reason": finish_reason or ("tool_calls" if tool_calls else "stop")}],
                "usage": usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
            resp_cache.set(exact_key, cached_resp)
            cache.remember(
                session.project_id,
                session.session_id,
                make_session_summary(cleaned, content),
            )
        stats.audit.log(_audit_record(
            request_id, session, tier, overlap,
            {"cache": "miss-stream", "clarify": clarify_mode,
             "route": {"name": route_name, "source": route_source, "score": route_score}},
        ))

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"X-Gateway-Cache": "MISS", "X-Gateway-Project-Id": session.project_id,
                 **perm_header},
    )


def _reply_text(data: Dict) -> str:
    choices = data.get("choices") or []
    if not choices:
        return ""
    msg = choices[0].get("message", {}) or {}
    return msg.get("content") or ""


def _now_ms() -> int:
    return int(time.time() * 1000)
