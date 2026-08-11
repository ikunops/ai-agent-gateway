import json
import time
import uuid
from typing import Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.config import Config, upstream_api_key
from app.layers.cache import CacheEngine
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

    if not config.upstream.base_url:
        raise HTTPException(
            status_code=400,
            detail="网关为纯整形模式（未配置 upstream.base_url）：请使用 POST /v1/refine "
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
    model: str = payload_in.get("model", config.upstream.default_model)

    session = parse_session(dict(request.headers), messages)
    cleaned = clean_messages(messages)
    request_id = f"req_{uuid.uuid4().hex[:12]}"
    upstream = UpstreamClient(config.upstream.base_url, upstream_api_key(config) or "sk-none")

    exact_key = request_cache_key(body)
    cached = resp_cache.get(exact_key)
    if cached:
        stats_hit(cached)
        stats.audit.log(_audit_record(request_id, session, None, 0, {"cache": "exact-hit"}))
        resp_headers = {"X-Gateway-Cache": "HIT", "X-Gateway-Project-Id": session.project_id}
        if stream:
            return StreamingResponse(
                (_sse_packet(c) for c in build_sse_chunks(cached) + [{"done": True}]),
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
        "model": model,
        "messages": final_messages,
        "temperature": payload_in.get("temperature", 1.0),
        "max_tokens": payload_in.get("max_tokens"),
    }
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
        async for item in upstream.chat_completions(payload_out, stream=True):
            if item["type"] == "error":
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
                    content += delta.get("reasoning_content") or ""
                yield _sse_packet(chunk)
            elif item["type"] == "done":
                yield "data: [DONE]\n\n"
        stats.record_route(request_id, session.project_id, session.session_id, tier)
        if content or usage:
            cached_resp = {
                "id": request_id,
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                             "finish_reason": "stop"}],
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
