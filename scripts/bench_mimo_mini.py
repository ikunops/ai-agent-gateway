# -*- coding: utf-8 -*-
"""mimo 小批量验证。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.bench_mimo_reasoning import judge_mimo
from scripts.bench_questions import BENCH_100


async def main():
    ok = fail = 0
    for expect, q in BENCH_100[:15]:
        r, raw = await judge_mimo(q)
        good = r == expect
        ok += good
        fail += not good
        tag = "OK " if good else "MISS"
        print(f"{tag} expect={expect} got={r!r} | {q[:30]}")
    print(f"mini result: {ok}/15 ({ok / 15:.0%})")


if __name__ == "__main__":
    asyncio.run(main())
