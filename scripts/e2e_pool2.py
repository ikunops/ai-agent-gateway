# -*- coding: utf-8 -*-
"""验证新分类器池: 触发 LLM 路由, 观察 stage。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

BASE = "http://127.0.0.1:8901/v1"
QS = [
    "帮我看一下甲骨文数据库的备份策略怎么设计",
    "写一个产品功能介绍的文案给我参考",
    "帮我分析一下负载均衡的几种算法",
]


def main():
    for i, q in enumerate(QS, 1):
        body = {
            "model": "deepseek-v4-flash",
            "messages": [{"role": "user", "content": q}],
            "max_tokens": 30,
        }
        t0 = time.time()
        try:
            r = httpx.post(f"{BASE}/chat/completions", json=body,
                           headers={"X-API-Key": "gateway-dev-key"}, timeout=120)
            dt = time.time() - t0
            if r.status_code == 200:
                data = r.json()
                print(f"q{i} OK ({dt:.1f}s) finish={data['choices'][0]['finish_reason']}")
            else:
                print(f"q{i} HTTP {r.status_code} ({dt:.1f}s)")
        except Exception as e:
            print(f"q{i} EXC ({time.time() - t0:.1f}s) {type(e).__name__}: {str(e)[:80]}")
        time.sleep(1)


if __name__ == "__main__":
    main()
