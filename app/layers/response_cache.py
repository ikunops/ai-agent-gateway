import hashlib
import json
import os
import re
import threading
import time
from typing import Dict, List, Optional, Tuple

_CACHE_MAX_BYTES = int(os.environ.get("GATEWAY_CACHE_MAX_BYTES", str(256 * 1024 * 1024)))
_SSE_CHUNK_CHARS = 200

_PRICES = {
    "deepseek-chat": (0.14, 0.28),
    "deepseek-reasoner": (0.55, 2.19),
    "deepseek-v4-flash": (0.14, 0.28),
    "deepseek-v4-pro": (0.435, 0.87),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "claude-3-5-sonnet": (3.00, 15.00),
    "glm-5.2": (1.40, 4.40),
    "kimi-k3": (3.00, 15.00),
    "qwen3.8-max": (2.00, 6.00),
    "hy3": (0.14, 0.58),
}

_LOCK = threading.Lock()
_stats: Dict = {
    "hits": 0,
    "misses": 0,
    "bytes_saved": 0,
    "tokens_saved": 0,
    "usd_saved": 0.0,
    "started": time.time(),
}


def estimate_usd(model: str, usage: Dict) -> float:
    pin, pout = _PRICES.get(model, (0.14, 0.28))
    tin = int(usage.get("prompt_tokens") or 0)
    tout = int(usage.get("completion_tokens") or 0)
    return (tin / 1e6) * pin + (tout / 1e6) * pout


def request_cache_key(body: bytes) -> str:
    try:
        obj = json.loads(body)
    except Exception:
        return hashlib.sha256(body).hexdigest()
    obj.pop("stream", None)
    canonical = json.dumps(obj, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ResponseCache:
    def __init__(self, cache_dir: str, max_bytes: int = _CACHE_MAX_BYTES):
        self.cache_dir = cache_dir
        self.max_bytes = max_bytes

    def _path(self, key: str) -> str:
        return os.path.join(self.cache_dir, key[:2], key + ".json")

    def get(self, key: str) -> Optional[Dict]:
        p = self._path(key)
        if not os.path.isfile(p):
            return None
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def set(self, key: str, response: Dict) -> None:
        try:
            os.makedirs(os.path.dirname(self._path(key)), exist_ok=True)
            tmp = self._path(key) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(response, f, ensure_ascii=False)
            os.replace(tmp, self._path(key))
            self._prune()
        except Exception:
            pass

    def clear(self) -> int:
        n = 0
        for root, _dirs, names in os.walk(self.cache_dir):
            for name in names:
                if name.endswith(".json"):
                    try:
                        os.remove(os.path.join(root, name))
                        n += 1
                    except Exception:
                        pass
        return n

    def _prune(self) -> None:
        try:
            total = 0
            files: List[Tuple[float, str]] = []
            for root, _dirs, names in os.walk(self.cache_dir):
                for n in names:
                    if not n.endswith(".json"):
                        continue
                    p = os.path.join(root, n)
                    total += os.path.getsize(p)
                    files.append((os.path.getmtime(p), p))
            if total <= self.max_bytes:
                return
            files.sort()
            for _mtime, p in files[: max(1, len(files) // 10)]:
                try:
                    os.remove(p)
                except Exception:
                    pass
        except Exception:
            pass


def stats_hit(cached: Dict) -> None:
    with _LOCK:
        _stats["hits"] += 1
        _stats["bytes_saved"] += len(json.dumps(cached).encode("utf-8"))
        usage = cached.get("usage")
        if isinstance(usage, dict):
            tin = int(usage.get("prompt_tokens") or 0)
            tout = int(usage.get("completion_tokens") or 0)
            _stats["tokens_saved"] += tin + tout
            _stats["usd_saved"] += estimate_usd(cached.get("model", ""), usage)


def stats_miss() -> None:
    with _LOCK:
        _stats["misses"] += 1


def stats_snapshot() -> Dict:
    with _LOCK:
        s = dict(_stats)
        s["hit_rate"] = round(s["hits"] / max(1, s["hits"] + s["misses"]), 4)
        s["usd_saved"] = round(s["usd_saved"], 4)
        s["uptime_s"] = round(time.time() - s["started"])
        return s


def build_sse_chunks(cached: Dict) -> List[Dict]:
    choices = cached.get("choices") or []
    message = choices[0].get("message", {}) if choices else {}
    content = message.get("content") or ""
    created = int(cached.get("created", time.time()))
    cid = cached.get("id", "chatcmpl-cache")
    model = cached.get("model", "")
    chunks: List[Dict] = []
    chunks.append({
        "id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
        "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
    })
    for i in range(0, len(content), _SSE_CHUNK_CHARS):
        chunks.append({
            "id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
            "choices": [{"index": 0, "delta": {"content": content[i:i + _SSE_CHUNK_CHARS]},
                         "finish_reason": None}],
        })
    chunks.append({
        "id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    })
    return chunks


def parse_stream_usage(chunk: Dict) -> Optional[Dict]:
    u = chunk.get("usage")
    return u if isinstance(u, dict) else None


_TRAILING_RE = re.compile(r"^(role|assistant|system|user)\s*[:：]\s*$", re.I)


def make_session_summary(messages: List[Dict], reply_content: str, max_chars: int = 600) -> str:
    user_lines: List[str] = []
    for m in messages:
        if m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str) and c.strip():
                user_lines.append(_first_meaningful_line(c))
    summary = f"用户最近请求: {' | '.join(user_lines[-5:])}\n" if user_lines else ""
    if reply_content:
        reply = reply_content.strip().split("\n")[0][:200]
        summary += f"最后回复摘要: {reply}"
    return summary[:max_chars]


_FILLER_LINE_RE = re.compile(
    r"^(你好|您好|嗨|hi|hello|谢谢|感谢|麻烦|辛苦了|帮帮忙|好的|可以|嗯|ok|请问|打扰)"
    r"[，。,.!！\s]*$",
    re.I,
)


def _first_meaningful_line(text: str) -> str:
    """取首个非寒暄行（长文本首行常是问候语，跳过）。"""
    lines = text.strip().split("\n")
    for line in lines:
        s = line.strip()
        if s and not _FILLER_LINE_RE.match(s):
            return s[:120]
    return (lines[0] if lines else "")[:120]
