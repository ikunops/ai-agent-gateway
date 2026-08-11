import json
import time
import uuid
from typing import Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.config import Config, upstream_api_key
from app.layers.cache import CacheEngine
from app.layers.preprocess import auth_ok, clean_messages, parse_session
from app.layers.system_builder import is_theoretical_query, reorganize_messages
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
from app.layers.system_builder import is_theoretical_query, reorganize_messages
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

    theoretical = is_theoretical_query(cleaned)

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

    all_text = " ".join(
        m.get("content", "") for m in cleaned if isinstance(m.get("content"), str)
    )
    context_text = "\n".join(
        f"{m.get('role')}: {str(m.get('content', ''))[:300]}"
        for m in cleaned[-6:-1]
        if isinstance(m.get("content"), str)
    )
    route_name, route_score, route_source = await router.route(
        all_text, llm_judge=llm_router.judge, context=context_text
    )
    tier, tier_content = cache.resolve(session.project_id, session.session_id, route_name)
    routing_note = ""
    if tier_content:
        routing_note = f"已匹配项目上下文（Tier{tier}）。" if tier > 1 else f"领域路由[{route_name}]（{route_source}，得分{route_score:.2f}）。"

    final_messages = reorganize_messages(
        cleaned,
        anchor_prompt=config.anchor_prompt,
        project_context=tier_content,
        session_context="",
        routing_note=routing_note,
        skip_anchor=theoretical,
    )
    system_text = final_messages[0].get("content", "") if final_messages else ""
    prefix_key = f"{session.project_id}::{session.session_id}::{model}"
    overlap = stats.record_prefix(prefix_key, system_text)

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
                    {"cache": "miss", "route": {"name": route_name, "source": route_source, "score": route_score}},
                ))
                return JSONResponse(
                    content=data,
                    headers={"X-Gateway-Cache": "MISS", "X-Gateway-Project-Id": session.project_id},
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
            {"cache": "miss-stream", "route": {"name": route_name, "source": route_source, "score": route_score}},
        ))

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"X-Gateway-Cache": "MISS", "X-Gateway-Project-Id": session.project_id},
    )


def _reply_text(data: Dict) -> str:
    choices = data.get("choices") or []
    if not choices:
        return ""
    msg = choices[0].get("message", {}) or {}
    return msg.get("content") or ""


def _now_ms() -> int:
    return int(time.time() * 1000)
