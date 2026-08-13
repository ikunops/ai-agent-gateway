# -*- coding: utf-8 -*-
"""mimo-v2.5 准确性测试: 读 reasoning_content 判定真实分类能力。

mimo 是纯推理模型, content 永远空, 答案在 reasoning_content 里。
本脚本模拟"如果网关支持读 reasoning"时 mimo 的表现。
"""
import asyncio
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from scripts.bench_questions import BENCH_100

ROUTES = ["oracle", "mysql", "kubernetes", "linux", "network", "generic", "java"]
BASE = "https://opencode.ai/zen/go/v1"
MODEL = "mimo-v2.5"


def parse_from_reasoning(text: str) -> str | None:
    """在推理文本里找路线名: 找 '选xxx' / '选择xxx' / '路线是xxx' 等模式, 最后兜底找路线名出现。"""
    if not text:
        return None
    low = text.lower()
    # 模式1: 明确 "应选 xxx" / "选 xxx" / "路线: xxx"
    for pat in [r"(?:应?选|选择|路线[是为]|答案[是为])\s*[:：]?\s*([a-z0-9\-_]+)",
                r"([a-z0-9\-_]+)\s*(?:路线|答案|选项)"]:
        for m in re.finditer(pat, low):
            cand = m.group(1)
            if cand in ROUTES:
                return cand
    # 模式2: 最后出现的路线名 (推理结尾通常给出结论)
    last = None
    for name in ROUTES:
        idx = low.rfind(name)
        if idx != -1 and (last is None or idx > last[1]):
            last = (name, idx)
    return last[0] if last else None


async def judge_mimo(q: str) -> tuple[str | None, str]:
    prompt = (
        f"你有以下路线：{', '.join(ROUTES)}。\n"
        f"用户问题：\"{q}\"\n"
        f"规则：根据问题涉及的技术领域选择最匹配的一条路线。\n"
        f"请只输出最匹配的一条路线名，不要输出其他内容。"
    )
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "你是一个路由分类器，只输出路线名。"},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 256,
        "temperature": 0,
        "stream": False,
    }
    key = os.environ.get("OPENCODE_GO_API_KEY", "")
    async with httpx.AsyncClient(timeout=120) as cl:
        r = await cl.post(f"{BASE}/chat/completions", json=payload,
                          headers={"Authorization": f"Bearer {key}"})
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}"
        msg = (r.json().get("choices") or [{}])[0].get("message", {})
        content = msg.get("content") or ""
        reasoning = msg.get("reasoning_content") or ""
        if content.strip():
            return None, f"content-nonempty?{content[:40]!r}"
        return parse_from_reasoning(reasoning), reasoning


async def main():
    ok, fail = 0, 0
    per_route = {}
    misses = []
    for expect, q in BENCH_100:
        r, raw = await judge_mimo(q)
        per_route.setdefault(expect, [0, 0])
        per_route[expect][1] += 1
        if r == expect:
            ok += 1
            per_route[expect][0] += 1
        else:
            fail += 1
            misses.append((expect, r, q[:40]))
    rate = ok / len(BENCH_100)
    detail = " ".join(f"{k}:{v[0]}/{v[1]}" for k, v in per_route.items())
    print(f"mimo-v2.5 (reasoning-parse): hit {ok}/{len(BENCH_100)} ({rate:.1%})")
    print(f"  {detail}")
    for expect, got, q in misses[:10]:
        print(f"  [MISS] expect={expect} got={got!r} | {q}")
    if len(misses) > 10:
        print(f"  ... and {len(misses) - 10} more")


if __name__ == "__main__":
    asyncio.run(main())
