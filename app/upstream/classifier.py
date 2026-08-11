"""分类器客户端 + 多分类器交叉验证。

分类器 = 路由慢路径的 LLM 兜底（技术词/向量都未命中时，判断用户意图属于哪个领域）。
模型完全由用户决定：任意 OpenAI 兼容端点（Ollama 本地免费 / DeepSeek / 中转站 / 自建），
密钥可空（本地与中转站通常不校验）；网关不绑定任何特定模型厂商。
"""

import asyncio
import re
import time
from typing import Dict, List, Optional, Tuple

import httpx


class ClassifierClient:
    """单个 OpenAI 兼容分类器端点。"""

    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str = "",
        model: str = "qwen2.5:7b",
        weight: float = 1.0,
        timeout: float = 30.0,
    ):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.weight = weight
        self.timeout = timeout

    async def judge(
        self, text: str, route_names: List[str], context: str = ""
    ) -> Optional[str]:
        result = await self.judge_detailed(text, route_names, context)
        if not result:
            return None
        name, _ = result
        return name

    async def judge_detailed(
        self, text: str, route_names: List[str], context: str = ""
    ) -> Optional[Tuple[str, Dict]]:
        """返回 (route_name, {latency_ms})；失败/超时返回 None。"""
        if not route_names:
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
            "max_tokens": 256,
            "temperature": 0,
            "stream": False,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        t0 = time.time()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout)) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions", json=payload, headers=headers
                )
                if resp.status_code != 200:
                    return None
                data = resp.json()
                content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
                name = self._parse(content, route_names)
                if not name:
                    return None
                return name, {"latency_ms": int((time.time() - t0) * 1000)}
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


class ClassifierEnsemble:
    """多分类器交叉验证：并发调用全部配置的分类器，按加权票选路线。

    - 全部一致 → 该路线，agreement = 1.0
    - 不一致 → 加权票数最高者胜出，agreement = 得票权重占比（路由得分随一致性下降）
    - 全部失败 → None，调用方自然降级（技术词/向量路径不受影响）
    - 单分类器也可以直接用 ClassifierClient，无需 Ensemble
    """

    def __init__(self, classifiers: List[ClassifierClient]):
        self.classifiers = classifiers

    @property
    def total_weight(self) -> float:
        return sum(c.weight for c in self.classifiers) or 1.0

    async def judge(
        self, text: str, route_names: List[str], context: str = ""
    ) -> Optional[str]:
        result = await self.judge_detailed(text, route_names, context)
        if not result:
            return None
        name, _ = result
        return name

    async def judge_detailed(
        self, text: str, route_names: List[str], context: str = ""
    ) -> Optional[Tuple[str, Dict]]:
        """返回 (route_name, {votes, per_classifier, agreement})。"""
        if not self.classifiers or not route_names:
            return None
        results = await asyncio.gather(
            *(c.judge_detailed(text, route_names, context) for c in self.classifiers),
            return_exceptions=True,
        )
        votes: Dict[str, float] = {}
        per: List[Dict] = []
        for c, r in zip(self.classifiers, results):
            if isinstance(r, Exception) or r is None:
                per.append({"name": c.name, "ok": False, "vote": None})
                continue
            name, detail = r
            votes[name] = votes.get(name, 0.0) + c.weight
            per.append({
                "name": c.name, "ok": True, "vote": name,
                "latency_ms": detail.get("latency_ms", 0),
            })
        if not votes:
            return None, {"votes": {}, "per_classifier": per, "agreement": 0.0}
        best = max(votes, key=votes.get)
        meta = {
            "votes": {k: round(v, 3) for k, v in votes.items()},
            "per_classifier": per,
            "agreement": round(votes[best] / self.total_weight, 3),
        }
        return best, meta
