import json
from typing import AsyncIterator, Dict

import httpx


class UpstreamClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def chat_completions(
        self, payload: Dict, stream: bool
    ) -> AsyncIterator[Dict]:
        body = {**payload}
        if stream:
            body["stream"] = True
        url = f"{self.base_url}/chat/completions"
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0)) as client:
            async with client.stream("POST", url, json=body, headers=self._headers()) as resp:
                if resp.status_code >= 400:
                    err_body = (await resp.aread()).decode("utf-8", "replace")
                    yield {"type": "error", "status": resp.status_code, "body": err_body}
                    return
                if stream:
                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[len("data:") :].strip()
                        if data == "[DONE]":
                            yield {"type": "done"}
                            return
                        try:
                            yield {"type": "chunk", "data": json.loads(data)}
                        except json.JSONDecodeError:
                            continue
                else:
                    content = (await resp.aread()).decode("utf-8", "replace")
                    yield {"type": "complete", "data": json.loads(content), "status": resp.status_code}
