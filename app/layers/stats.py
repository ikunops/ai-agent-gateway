import json
import threading
import time
from pathlib import Path
from typing import Dict, List

_START = time.time()


class _SafeCounter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.data: Dict[str, Dict] = {}

    def _bucket(self, key: str) -> Dict:
        day = time.strftime("%Y-%m-%d", time.localtime())
        dkey = f"{day}::{key}"
        if dkey not in self.data:
            self.data[dkey] = {
                "requests": 0,
                "tokens": 0,
                "hit_bytes": 0,
                "total_prefix": 0,
                "saved_tokens": 0,
            }
        return self.data[dkey]

    def inc(self, key: str, **fields) -> None:
        with self._lock:
            bucket = self._bucket(key)
            bucket["requests"] += 1
            for name, val in fields.items():
                if name in bucket:
                    bucket[name] += val

    def snapshot(self) -> Dict:
        with self._lock:
            return dict(self.data)


class AuditLogger:
    def __init__(self, directory: str, keep_days: int = 30):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.keep_days = keep_days
        self._lock = threading.Lock()

    def log(self, record: Dict) -> None:
        with self._lock:
            fname = time.strftime("audit-%Y-%m-%d.jsonl", time.localtime())
            f = self.dir / fname
            with open(f, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._prune()

    def _prune(self) -> None:
        if self.keep_days <= 0:
            return
        cutoff = time.time() - self.keep_days * 86400
        for f in self.dir.glob("audit-*.jsonl"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass


class Stats:
    def __init__(self, audit_dir: str, keep_days: int = 30):
        self.counter = _SafeCounter()
        self.audit = AuditLogger(audit_dir, keep_days)
        self._prefix_history: Dict[str, str] = {}

    def record_route(self, request_id: str, project_id: str, session_id: str, tier: int) -> None:
        self.counter.inc("routes", tier=tier if tier else 0, requests=1)
        self.counter.inc(f"tier{tier}", requests=1)

    def record_clarify(self) -> None:
        """需求澄清模式触发计数（评估拦截了多少模糊需求）。"""
        self.counter.inc("clarify", requests=1)

    def record_saved(self, saved_chars: int = 0, saved_tokens: int = 0) -> None:
        self.counter.inc("savings", saved_tokens=saved_tokens, requests=1)
        if saved_chars:
            self.counter.inc("savings", hit_bytes=saved_chars)

    def record_prefix(self, key: str, current: str) -> int:
        prev = self._prefix_history.get(key, "")
        overlap = 0
        if prev:
            i = 0
            while i < min(len(prev), len(current)) and prev[i] == current[i]:
                i += 1
            overlap = i
            self.counter.inc("prefix", hit_bytes=overlap)
        self._prefix_history[key] = current
        return overlap

    def record_completion(self, request_id: str, tokens: int, latency_ms: int) -> None:
        self.counter.inc("completions", tokens=tokens, requests=1)

    def snapshot(self) -> Dict:
        return {
            "uptime_seconds": int(time.time() - _START),
            "buckets": self.counter.snapshot(),
        }
