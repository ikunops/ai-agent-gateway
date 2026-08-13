# -*- coding: utf-8 -*-
"""多领域命中率测试(用 timeout=90 复刻生产配置修复后)。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.upstream.classifier import ClassifierClient

ZEN = "https://opencode.ai/zen/v1"
ROUTES = ["oracle", "mysql", "kubernetes", "linux", "network", "generic", "java"]

CASES = [
    ("java", "用 Java 写一个线程池，怎么避免 OOM？", "dev-lang"),
    ("java", "Spring Boot 的 @Transactional 失效是什么原因？", "dev-lang"),
    ("kubernetes", "Pod 一直 CrashLoopBackOff，怎么排查？", "ops"),
    ("kubernetes", "K8s 里 Deployment 滚动更新卡住了怎么办？", "ops"),
    ("oracle", "Oracle 的 AWR 报告怎么分析？", "db"),
    ("mysql", "MySQL 慢查询怎么优化索引？", "db"),
    ("linux", "服务器 CPU 飙高，怎么定位是哪个进程？", "ops"),
    ("network", "两台机器 ping 不通，从哪开始排查？", "network"),
    ("generic", "今天中午吃什么好？", "daily"),
    ("generic", "帮我写一段产品文案，介绍我们的新软件。", "daily"),
    ("generic", "前端 UI 用 Tailwind 怎么做出玻璃拟态卡片效果？", "frontend"),
    ("generic", "为什么天空是蓝色的？", "science"),
]


async def test_one(client, cases):
    ok, fail, per_route = 0, 0, {}
    for expect, q, tag in cases:
        try:
            r = await client.judge(q, ROUTES)
        except Exception:
            r = None
        per_route.setdefault(tag, [0, 0])
        per_route[tag][1] += 1
        if r == expect:
            ok += 1
            per_route[tag][0] += 1
        else:
            fail += 1
            print(f"  [MISS] {tag}: expect={expect} got={r!r}")
    return ok, fail, per_route


async def main():
    results = []
    for model in ["laguna-s-2.1-free", "nemotron-3-ultra-free", "deepseek-v4-flash-free"]:
        c = ClassifierClient(model, ZEN, "", model, 1.0, timeout=90.0, local=True)
        print(f"=== {model} ===")
        ok, fail, per_route = await test_one(c, CASES)
        rate = ok / len(CASES)
        detail = " ".join(f"{k}:{v[0]}/{v[1]}" for k, v in per_route.items())
        print(f"  => hit {ok}/{len(CASES)} ({rate:.0%}) | {detail}")
        results.append((model, ok, len(CASES), rate))
        await asyncio.sleep(1)

    print("=" * 50)
    for model, ok, total, rate in sorted(results, key=lambda x: -x[3]):
        flag = "OK" if rate >= 0.5 else "BAD"
        print(f"  [{flag}] {model}: {ok}/{total} ({rate:.0%})")


if __name__ == "__main__":
    asyncio.run(main())
