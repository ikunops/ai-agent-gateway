# -*- coding: utf-8 -*-
"""kilo free 候选模型快速筛选: 20 题小样本 + 状态码。

用法: python scripts/bench_kilo_candidates.py
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from scripts.bench_questions import BENCH_100

BASE = "https://api.kilo.ai/api/openrouter"
ROUTES = ["oracle", "mysql", "kubernetes", "linux", "network", "generic", "java"]

CANDIDATES = [
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "poolside/laguna-xs-2.1:free",
    "stepfun/step-3.7-flash:free",
    "tencent/hy3:free",
    "liquid/lfm-2.5-2.6b:free",
]

SAMPLE = BENCH_100[:20]


def parse(content: str) -> str | None:
    c = (content or "").strip().lower()
    for n in ROUTES:
        if n in c:
            return n
    return None


async def judge_once(model: str, q: str) -> tuple[str | None, int, float]:
    prompt = (
        f"你有以下路线：{', '.join(ROUTES)}。\n"
        f"用户问题：\"{q}\"\n"
        f"规则：根据问题涉及的技术领域选择最匹配的一条路线。\n"
        f"请只输出最匹配的一条路线名，不要输出其他内容。"
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是一个路由分类器，只输出路线名。"},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 64,
        "temperature": 0,
        "stream": False,
    }
    t0 = time.time()
    async with httpx.AsyncClient(timeout=60, trust_env=False) as cl:
        r = await cl.post(f"{BASE}/chat/completions", json=payload)
        dt = time.time() - t0
        if r.status_code != 200:
            return None, r.status_code, dt
        content = (r.json().get("choices") or [{}])[0].get("message", {}).get("content", "")
        return parse(content), 200, dt


async def test_model(model: str) -> None:
    ok, n_200, n_err = 0, 0, 0
    err_types = {}
    misses = []
    for expect, q in SAMPLE:
        got, status, dt = await judge_once(model, q)
        if status == 200:
            n_200 += 1
            if got == expect:
                ok += 1
            else:
                misses.append((expect, got, q[:30]))
        else:
            n_err += 1
            err_types[status] = err_types.get(status, 0) + 1
        await asyncio.sleep(1.5)
    rate = ok / len(SAMPLE)
    rate_200 = ok / max(1, n_200)
    print(f"=== {model}")
    print(f"  hit {ok}/{len(SAMPLE)} ({rate:.0%}) | 200:{n_200} err:{n_err} {err_types or ''} | 200命中率 {rate_200:.0%}")
    for e, g, q in misses[:4]:
        print(f"     [MISS] expect={e} got={g!r} | {q}")


async def main():
    for m in CANDIDATES:
        try:
            await test_model(m)
        except Exception as e:
            print(f"=== {m} EXC {type(e).__name__}: {str(e)[:80]}")
        await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main())
