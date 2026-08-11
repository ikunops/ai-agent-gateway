"""本地免费模型（Ollama）抽取式选段器。

只做"选段"不做"改写"：模型输出原文段落编号，网关逐字校验后才采纳。
摘要仅用于路由决策（选路线），转发给上游的原文永远不变。
不可用 / 超时 / 返回幻觉 → 返回 None，由调用方回退确定性路径。
"""

import re
from typing import List, Optional

import httpx

from app.layers.text_analysis import split_segments


class LocalDigestPicker:
    def __init__(
        self,
        base_url: str,
        model: str,
        max_segments: int = 4,
        timeout: float = 15.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.max_segments = max_segments
        self.timeout = timeout
        self._error: Optional[str] = None

    @property
    def error(self) -> Optional[str]:
        return self._error

    async def pick(self, text: str) -> Optional[List[str]]:
        segs = split_segments(text)
        if len(segs) < 3:
            return None
        lines = "\n".join(f"{i}. {s[:200]}" for i, s in enumerate(segs, 1))
        prompt = (
            "从下面段落中，选出对'技术领域判断'最关键、且删掉会改变语义的段落编号。\n"
            "规则：含否定词（不/没/别/非/禁）、连接词（和/或者/与）、"
            "代码符号（&& || => :: !=）的段落必须选。\n"
            "只输出编号（逗号分隔），不要输出其他内容；无法判断输出 0。\n\n"
            f"{lines}"
        )
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"temperature": 0},
                    },
                )
                if resp.status_code != 200:
                    self._error = f"status {resp.status_code}"
                    return None
                content = (resp.json().get("response") or "").strip()
        except Exception as e:
            self._error = str(e)
            return None
        try:
            idxs = sorted({int(x) for x in re.findall(r"\d+", content) if int(x) > 0})
        except ValueError:
            return None
        if not idxs:
            return None
        picked = [segs[i - 1] for i in idxs[: self.max_segments] if 0 < i <= len(segs)]
        return picked or None
