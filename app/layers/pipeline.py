"""L1→L4 共享管线：清洗 → 意图检测（理论/模糊澄清）→ 路由 → 四层降级 → System 构造 → Ledger。

转发模式（app/api/chat.py）与整形模式（app/api/refine.py）共用，产出完全一致。
整形模式是主形态：网关不持有任何模型 key、不选模型、不替模型回复——
只输出"增强后的请求 + 路由元信息"，由 Agent 侧（opencodego）决定发给用户当前选择的模型。
"""

import time
from typing import Dict, List

from app.layers.permissions import check_permission
from app.layers.system_builder import (
    is_theoretical_query,
    is_vague_development_request,
    reorganize_messages,
)


async def process_request(
    *,
    cleaned: List[Dict],
    session,
    model: str,
    config,
    cache,
    router,
    llm_router,
    stats,
    routing_ledger,
    request_id: str,
) -> Dict:
    """执行完整管线，返回最终消息与决策元信息。零上游模型调用（分类器缺省时自动降级）。"""
    theoretical = is_theoretical_query(cleaned)

    # 模糊需求澄清模式：每会话最多一轮，跳过整个 L2 路由
    clarify_mode = False
    if is_vague_development_request(cleaned):
        if not cache.already_clarified(session.project_id, session.session_id):
            cache.mark_clarified(session.project_id, session.session_id)
            clarify_mode = True
            stats.record_clarify()

    user_text = " ".join(
        m.get("content", "")
        for m in cleaned
        if m.get("role") == "user" and isinstance(m.get("content"), str)
    )

    permission = check_permission(user_text, getattr(config, "permission_level", "L1"))

    if clarify_mode:
        route_name, route_score, route_source = "", 0.0, "clarify-skip"
        route_meta = {
            "terms": [], "segments": 0, "digest_len": 0, "fidelity_forced": 0,
            "vector_latency_ms": 0, "judge_latency_ms": 0, "vector_scores": {},
        }
        tier, tier_content = 4, ""
        routing_note = ""
    else:
        context_text = "\n".join(
            f"{m.get('role')}: {str(m.get('content', ''))[:300]}"
            for m in cleaned[-6:-1]
            if isinstance(m.get("content"), str)
        )
        judge = llm_router.judge if llm_router is not None else None
        route_name, route_score, route_source, route_meta = await router.route_detailed(
            user_text, llm_judge=judge, context=context_text
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
        skip_anchor=theoretical or clarify_mode,
        clarification_hint=config.clarify_prompt if clarify_mode else "",
    )
    system_text = final_messages[0].get("content", "") if final_messages else ""
    overlap = stats.record_prefix(
        f"{session.project_id}::{session.session_id}::{model}", system_text
    )

    routing_ledger.log({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "request_id": request_id,
        "project_id": session.project_id,
        "session_id": session.session_id,
        "text_len": len(user_text),
        "segments": route_meta.get("segments", 0),
        "terms": route_meta.get("terms", []),
        "source": route_source,
        "route": route_name,
        "score": round(route_score, 3),
        "tier": tier,
        "clarify": clarify_mode,
        "digest_len": route_meta.get("digest_len", 0),
        "fidelity_forced": route_meta.get("fidelity_forced", 0),
        "vector_latency_ms": route_meta.get("vector_latency_ms", 0),
        "judge_latency_ms": route_meta.get("judge_latency_ms", 0),
        "vector_scores": route_meta.get("vector_scores", {}),
        "votes": route_meta.get("vote", {}).get("votes", {}),
        "agreement": route_meta.get("vote", {}).get("agreement", 0),
        "perm_action": permission["action"],
        "perm_matched": [m["label"] for m in permission.get("matched", [])],
    })

    return {
        "messages": final_messages,
        "system_text": system_text,
        "tier": tier,
        "route_name": route_name,
        "route_score": route_score,
        "route_source": route_source,
        "route_meta": route_meta,
        "clarify": clarify_mode,
        "theoretical": theoretical,
        "overlap": overlap,
        "user_text": user_text,
        "permission": permission,
    }
