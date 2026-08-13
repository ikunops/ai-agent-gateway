import json
from typing import AsyncIterator, Dict

import httpx

CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 180.0
WRITE_TIMEOUT = 30.0
STREAM_IDLE_TIMEOUT = 60.0


class UpstreamClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key and self.api_key not in ("", "sk-none"):
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def chat_completions(
        self, payload: Dict, stream: bool
    ) -> AsyncIterator[Dict]:
        body = {**payload}
        if stream:
            body["stream"] = True
        url = f"{self.base_url}/chat/completions"
        timeout = httpx.Timeout(
            connect=CONNECT_TIMEOUT,
            read=READ_TIMEOUT,
            write=WRITE_TIMEOUT,
            pool=30.0,
        )
        async with httpx.AsyncClient(timeout=timeout, proxy=None) as client:
            try:
                async with client.stream("POST", url, json=body, headers=self._headers()) as resp:
                    if resp.status_code >= 400:
                        err_body = (await resp.aread()).decode("utf-8", "replace")
                        yield {"type": "error", "status": resp.status_code, "body": err_body}
                        return
                    if stream:
                        empty_wait = 0.0
                        async for line in resp.aiter_lines():
                            if not line or not line.startswith("data:"):
                                continue
                            empty_wait = 0.0
                            data = line[len("data:") :].strip()
                            if data == "[DONE]":
                                yield {"type": "done"}
                                return
                            try:
                                yield {"type": "chunk", "data": json.loads(data)}
                            except json.JSONDecodeError:
                                continue
                        yield {"type": "done"}
                    else:
                        content = (await resp.aread()).decode("utf-8", "replace")
                        yield {"type": "complete", "data": json.loads(content), "status": resp.status_code}
            except httpx.TimeoutException:
                yield {"type": "error", "status": 504, "body": "upstream timeout"} if not stream else {
                    "type": "error", "status": 504, "body": "upstream timeout"
                }
            except httpx.HTTPError as e:
                yield {"type": "error", "status": 502, "body": f"upstream error: {e}"}
