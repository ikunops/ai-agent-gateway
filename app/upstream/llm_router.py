import json
import re
from typing import List, Optional

import httpx

from app.layers.router import SemanticRouter


class LLMRouter:
    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    async def judge(
        self, text: str, route_names: List[str], context: str = ""
    ) -> Optional[str]:
        if not self.api_key or not route_names:
            return None
        routes = ", ".join(route_names)
        prompt = (
            f"你有以下路线：{routes}。\n"
            f"用户问题：\"{text[:500]}\"\n"
        )
        if context.strip():
            prompt += (
                f"对话上下文（用户之前讨论的内容，用于判断语境，优先级最高）："
                f"\"{context[-800:]}\"\n"
            )
        prompt += (
            f"规则：\n"
            f"1. 如果提供了对话上下文，必须优先依据上下文判断技术语境，把上下文中最突出的技术领域作为路线。\n"
            f"   例如：上下文在讨论 MySQL/数据库问题，则\"分布式事务\"应选 mysql；上下文在讨论 Spring/JVM，则应选 java。\n"
            f"2. 仅在没有任何上下文时，理论性问题（什么是/解释/原理）才选 generic。\n"
            f"3. 否则根据问题涉及的技术领域选择最匹配的一条路线。\n"
            f"请只输出最匹配的一条路线名，不要输出其他内容。"
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "你是一个路由分类器，只输出路线名。"},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 16,
            "temperature": 0,
            "stream": False,
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                )
                if resp.status_code != 200:
                    return None
                data = resp.json()
                content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
                return self._parse(content, route_names)
        except Exception:
            return None

    @staticmethod
    def _parse(content: str, route_names: List[str]) -> Optional[str]:
        content = (content or "").strip().lower()
        for name in route_names:
            if name.lower() in content:
                return name
        match = re.search(r"[a-z0-9\-_]+", content)
        if match and match.group(0).lower() in [n.lower() for n in route_names]:
            return match.group(0)
        return None
