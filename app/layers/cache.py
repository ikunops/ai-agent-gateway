import hashlib
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

AGENTS_FILENAME = "AGENTS.md"

_TECH_STACK_RE = re.compile(
    r"(?i)\b(java|python|golang|go|rust|typescript|javascript|node|react|vue|"
    r"mysql|postgres|oracle|mongodb|redis|kafka|kubernetes|k8s|docker|nginx|linux|"
    r"fastapi|django|flask|spring|vue|prometheus|grafana)\b"
)


class TierResult:
    def __init__(self, tier: int, content: str):
        self.tier = tier
        self.content = content

    @property
    def hit(self) -> bool:
        return bool(self.content.strip())

    def __repr__(self) -> str:
        return f"TierResult(tier={self.tier}, hit={self.hit}, len={len(self.content)})"


class SessionCache:
    def __init__(self, max_sessions: int = 200):
        self._data: "OrderedDict[str, str]" = OrderedDict()
        self._max = max_sessions
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            if key not in self._data:
                return None
            self._data.move_to_end(key)
            return self._data[key]

    def set(self, key: str, summary: str) -> None:
        if not summary.strip():
            return
        with self._lock:
            self._data[key] = summary
            self._data.move_to_end(key)
            while len(self._data) > self._max:
                self._data.popitem(last=False)


class CacheEngine:
    def __init__(self, project_root: Optional[Path] = None, max_sessions: int = 200):
        self.project_root = project_root
        self.sessions = SessionCache(max_sessions=max_sessions)
        self.registry = None

    def bind_registry(self, registry) -> None:
        self.registry = registry

    def _tier1(self, tags: List[str]) -> str:
        if not tags:
            return ""
        return (
            "通用规范：\n"
            + "\n".join(f"- {tag} 相关问题优先给出根因分析，再给解决方案" for tag in tags)
        )

    def _tier2(self, project_id: str) -> str:
        if self.registry:
            f = self.registry.agents_file(project_id)
            if f:
                try:
                    return f.read_text(encoding="utf-8")[:20000]
                except OSError:
                    return ""
        if not self.project_root:
            return ""
        for base in (self.project_root / project_id, self.project_root):
            f = base / AGENTS_FILENAME
            if f.is_file():
                try:
                    return f.read_text(encoding="utf-8")[:20000]
                except OSError:
                    return ""
        return ""

    def resolve(
        self, project_id: str, session_id: str, tags: List[str]
    ) -> Tuple[int, str]:
        tier1 = self._tier1(tags)
        if tier1.strip():
            return 1, tier1

        tier2 = self._tier2(project_id)
        if tier2.strip():
            return 2, tier2

        tier3 = self.sessions.get(self._session_key(project_id, session_id))
        if tier3:
            return 3, tier3

        return 4, ""

    @staticmethod
    def _session_key(project_id: str, session_id: str) -> str:
        return f"{project_id}::{session_id}"

    def remember(self, project_id: str, session_id: str, summary: str) -> None:
        self.sessions.set(self._session_key(project_id, session_id), summary)

    @staticmethod
    def fingerprint(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def extract_tech_tags(text: str) -> List[str]:
    found: List[str] = []
    seen = set()
    for m in _TECH_STACK_RE.finditer(text):
        tag = m.group(1).lower()
        if tag in ("go", "golang"):
            tag = "golang"
        if tag not in seen:
            seen.add(tag)
            found.append(tag)
    return found[:5]
