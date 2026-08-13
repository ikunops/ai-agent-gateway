# -*- coding: utf-8 -*-
"""zen-laguna 专项测试: 慢速批次 + 状态码记录 + 问题特征分析。

目标:
  1. 区分 限流失败(429) vs 真判错(200 但答错)
  2. 分析 laguna 在 长文本 / 多问题 / 短文本 上的表现差异
"""
import asyncio
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from scripts.bench_questions import BENCH_100

BASE = "https://opencode.ai/zen/v1"
MODEL = "deepseek-v4-flash-free"
ROUTES = ["oracle", "mysql", "kubernetes", "linux", "network", "generic", "java"]

BATCH_SLEEP = 3.0   # 每题间隔 (防限流)
RETRY_WAIT = 15.0   # 429 后重试等待


def question_features(q: str) -> dict:
    """问题特征: 长度 / 是否多问题 / 是否含标点复合。"""
    length = len(q)
    # 多问题: 含多个问号 或 "和/与/及" 连接多个疑问
    q_marks = q.count("？") + q.count("?")
    multi = q_marks >= 2 or bool(re.search(r"[？?].{0,20}[？?]", q))
    combined = bool(re.search(r"(和|与|及|、).{0,15}[？?]", q))
    return {
        "len": length,
        "long": length >= 40,
        "multi": multi or combined,
    }


def parse(content: str) -> str | None:
    content = (content or "").strip().lower()
    for name in ROUTES:
        if name.lower() in content:
            return name
    return None


async def judge_once(q: str) -> tuple[str | None, int, float]:
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
        "max_tokens": 64,
        "temperature": 0,
        "stream": False,
    }
    t0 = time.time()
    async with httpx.AsyncClient(timeout=60) as cl:
        r = await cl.post(f"{BASE}/chat/completions", json=payload)
        dt = time.time() - t0
        if r.status_code != 200:
            return None, r.status_code, dt
        content = (r.json().get("choices") or [{}])[0].get("message", {}).get("content", "")
        return parse(content), 200, dt


async def main():
    results = []  # (expect, got, status, latency, features)
    for i, (expect, q) in enumerate(BENCH_100, 1):
        feats = question_features(q)
        got, status, dt = await judge_once(q)
        # 429 重试一次
        if status == 429:
            await asyncio.sleep(RETRY_WAIT)
            got, status, dt = await judge_once(q)
        results.append((expect, got, status, dt, feats, q))
        mark = "OK " if (status == 200 and got == expect) else "MISS"
        print(f"{mark} [{i:3d}] status={status} {dt:5.1f}s len={feats['len']:2d} "
              f"long={feats['long']} multi={feats['multi']} | {q[:36]}", flush=True)
        await asyncio.sleep(BATCH_SLEEP)

    # ---- 汇总 ----
    total = len(results)
    ok_all = sum(1 for e, g, s, _, _, _q in results if s == 200 and g == e)
    rate_200 = sum(1 for e, g, s, _, _, _q in results if s == 200 and g == e)
    n_429 = sum(1 for _, _, s, _, _, _q in results if s == 429)
    n_other_err = sum(1 for _, _, s, _, _, _q in results if s not in (200, 429))
    n_200 = sum(1 for _, _, s, _, _, _q in results if s == 200)
    ok_200 = sum(1 for e, g, s, _, _, _q in results if s == 200 and g == e)

    print("\n" + "=" * 60)
    print(f"总数 {total} | 200响应 {n_200} | 429限流 {n_429} | 其他错误 {n_other_err}")
    print(f"200 命中率: {ok_200}/{n_200} ({ok_200 / max(1, n_200):.1%})")
    print(f"全量命中率(429算错): {rate_200}/{total} ({rate_200 / total:.1%})")

    # 长文本 vs 短文本
    long_ok = sum(1 for e, g, s, _, f, _q in results if s == 200 and g == e and f["long"])
    long_n = sum(1 for _, _, s, _, f, _q in results if s == 200 and f["long"])
    short_ok = sum(1 for e, g, s, _, f, _q in results if s == 200 and g == e and not f["long"])
    short_n = sum(1 for _, _, s, _, f, _q in results if s == 200 and not f["long"])
    print(f"\n长文本(>=40字): {long_ok}/{long_n} ({long_ok / max(1, long_n):.1%})")
    print(f"短文本(<40字): {short_ok}/{short_n} ({short_ok / max(1, short_n):.1%})")

    # 多问题 vs 单问题
    multi_ok = sum(1 for e, g, s, _, f, _q in results if s == 200 and g == e and f["multi"])
    multi_n = sum(1 for _, _, s, _, f, _q in results if s == 200 and f["multi"])
    print(f"多问题: {multi_ok}/{multi_n} ({multi_ok / max(1, multi_n):.1%})")
    print(f"单问题: {short_ok}/{short_n} ({short_ok / max(1, short_n):.1%})" if False else "")

    # 领域分布
    print("\n按领域(200响应内):")
    per = {}
    for e, g, s, _, _, _q in results:
        if s != 200:
            continue
        per.setdefault(e, [0, 0])
        per[e][1] += 1
        if g == e:
            per[e][0] += 1
    for k, (ok, n) in per.items():
        print(f"  {k}: {ok}/{n} ({ok / max(1, n):.1%})")

    # 200 但判错的样本
    wrong = [(e, g, q, f) for (e, g, s, _, f, q) in results if s == 200 and g != e]
    if wrong:
        print(f"\n200 但判错 {len(wrong)} 条:")
        for e, g, q, f in wrong[:10]:
            print(f"  expect={e} got={g} long={f['long']} multi={f['multi']} | {q[:44]}")


if __name__ == "__main__":
    asyncio.run(main())
