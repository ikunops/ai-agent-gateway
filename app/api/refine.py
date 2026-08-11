"""POST /v1/refine —— 纯整形端点（网关主形态）。

接收 OpenAI 兼容的 messages，返回"增强后的请求"与路由元信息。
网关不持有模型 key、不选模型、不替模型回复——发给哪个模型由 Agent 侧（opencodego）
决定：把 refined.messages 原样转发给用户当前选择的模型即可。
"""

import json
import time
import uuid
from typing import Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Request

from app.layers.pipeline import process_request
from app.layers.permissions import check_permission
from app.layers.preprocess import auth_ok, clean_messages, parse_session

router = APIRouter()


@router.post("/v1/refine")
async def refine(
    request: Request,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    authorization: Optional[str] = Header(default=None),
):
    config, stats, cache, router, llm_router, ledger = (
        request.app.state.config,
        request.app.state.stats,
        request.app.state.cache,
        request.app.state.router,
        request.app.state.llm_router,
        request.app.state.routing_ledger,
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
    if not messages:
        raise HTTPException(status_code=400, detail="messages required")

    session = parse_session(dict(request.headers), messages)
    cleaned = clean_messages(messages)
    request_id = f"req_{uuid.uuid4().hex[:12]}"

    result = await process_request(
        cleaned=cleaned,
        session=session,
        model=payload_in.get("model", ""),
        config=config,
        cache=cache,
        router=router,
        llm_router=llm_router,
        stats=stats,
        routing_ledger=ledger,
        request_id=request_id,
    )

    perm = dict(result["permission"])
    if x_perm := request.headers.get("x-permission-level", "").strip().upper():
        perm["level"] = x_perm
        perm = check_permission(result["user_text"], x_perm)

    meta: Dict = {
        "route": {
            "name": result["route_name"],
            "score": round(result["route_score"], 3),
            "source": result["route_source"],
        },
        "tier": result["tier"],
        "clarify": result["clarify"],
        "theoretical": result["theoretical"],
        "prefix_overlap": result["overlap"],
        "permission": perm,
    }
    rm = result["route_meta"]
    for k in ("terms", "segments", "digest_len", "fidelity_forced",
              "vector_latency_ms", "judge_latency_ms", "vector_scores"):
        if k in rm:
            meta[k] = rm[k]

    return {
        "request_id": request_id,
        "refined": {
            "model": payload_in.get("model"),
            "messages": result["messages"],
            "temperature": payload_in.get("temperature", 1.0),
            "max_tokens": payload_in.get("max_tokens"),
        },
        "meta": meta,
    }
