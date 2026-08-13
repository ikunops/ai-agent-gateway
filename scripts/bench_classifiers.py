# -*- coding: utf-8 -*-
"""100 题多模型路由分类基准测试。

用法: python scripts/bench_classifiers.py [模型配置]
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.upstream.classifier import ClassifierClient
from scripts.bench_questions import BENCH_100

ROUTES = ["oracle", "mysql", "kubernetes", "linux", "network", "generic", "java"]

# 每个模型: (名称, base_url, model_id, api_key_env, timeout)
CANDIDATES = [
    # go 便宜模型
    ("go-ds-flash", "https://opencode.ai/zen/go/v1", "deepseek-v4-flash", "OPENCODE_GO_API_KEY", 90),
    ("go-mimo25", "https://opencode.ai/zen/go/v1", "mimo-v2.5", "OPENCODE_GO_API_KEY", 90),
    # zen free
    ("zen-laguna", "https://opencode.ai/zen/v1", "laguna-s-2.1-free", "", 90),
    # kilo (免key)
    ("kilo-auto", "https://api.kilo.ai/api/openrouter", "kilo-auto/free", "", 90),
    ("kilo-laguna", "https://api.kilo.ai/api/openrouter", "poolside/laguna-s-2.1:free", "", 90),
    # zen big-pickle
    ("zen-bigpickle", "https://opencode.ai/zen/v1", "big-pickle", "", 90),
]


def load_api_key(env: str) -> str:
    import os
    return os.environ.get(env, "")


async def test_one(client, cases):
    ok, fail, per_route, misses = 0, 0, {}, []
    for expect, q in cases:
        try:
            r = await client.judge(q, ROUTES)
        except Exception:
            r = None
        per_route.setdefault(expect, [0, 0])
        per_route[expect][1] += 1
        if r == expect:
            ok += 1
            per_route[expect][0] += 1
        else:
            fail += 1
            misses.append((expect, r, q[:45]))
    return ok, fail, per_route, misses


async def main():
    only = sys.argv[1:] if len(sys.argv) > 1 else None
    results = []
    for name, base, model, key_env, timeout in CANDIDATES:
        if only and name not in only:
            continue
        key = load_api_key(key_env)
        c = ClassifierClient(name, base, key, model, 1.0, timeout=timeout, local=False)
        print(f"=== {name} ({model} @ {base}) ===", flush=True)
        ok, fail, per_route, misses = await test_one(c, BENCH_100)
        rate = ok / len(BENCH_100)
        detail = " ".join(f"{k}:{v[0]}/{v[1]}" for k, v in per_route.items())
        print(f"  => hit {ok}/{len(BENCH_100)} ({rate:.1%}) | {detail}", flush=True)
        if misses:
            shown = misses[:6]
            for expect, got, q in shown:
                print(f"     [MISS] expect={expect} got={got!r} | {q}")
            if len(misses) > 6:
                print(f"     ... and {len(misses) - 6} more misses")
        results.append((name, model, ok, len(BENCH_100), rate))
        await asyncio.sleep(1)

    print("\n" + "=" * 70)
    print("FINAL RANKING:")
    for name, model, ok, total, rate in sorted(results, key=lambda x: -x[4]):
        flag = "OK " if rate >= 0.8 else ("MID" if rate >= 0.5 else "BAD")
        print(f"  [{flag}] {name}: {ok}/{total} ({rate:.1%})  model={model}")


if __name__ == "__main__":
    asyncio.run(main())
