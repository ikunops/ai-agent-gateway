# -*- coding: utf-8 -*-
"""看 mimo-v2.5 原始响应。"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

ROUTES = ["oracle", "mysql", "kubernetes", "linux", "network", "generic", "java"]
Q = "Pod 一直 CrashLoopBackOff 怎么排查？"


async def main():
    key = os.environ.get("OPENCODE_GO_API_KEY", "")
    prompt = (
        f"你有以下路线：{', '.join(ROUTES)}。\n"
        f"用户问题：\"{Q}\"\n"
        f"规则：根据问题涉及的技术领域选择最匹配的一条路线。\n"
        f"请只输出最匹配的一条路线名，不要输出其他内容。"
    )
    payload = {
        "model": "mimo-v2.5",
        "messages": [
            {"role": "system", "content": "你是一个路由分类器，只输出路线名。"},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 256,
        "temperature": 0,
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=120) as cl:
        r = await cl.post("https://opencode.ai/zen/go/v1/chat/completions",
                          json=payload,
                          headers={"Authorization": f"Bearer {key}"})
        print("status:", r.status_code)
        if r.status_code == 200:
            data = r.json()
            msg = (data.get("choices") or [{}])[0].get("message", {})
            print(f"content[{len(msg.get('content') or '')}]: {msg.get('content')!r}")
            print(f"reasoning[{len(msg.get('reasoning_content') or '')}]: {(msg.get('reasoning_content') or '')[:150]!r}")
        else:
            print("body:", r.text[:200])


if __name__ == "__main__":
    asyncio.run(main())
