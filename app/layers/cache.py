import hashlib
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

AGENTS_FILENAME = "AGENTS.md"


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
        self._clarified: set = set()
        self._clarified_lock = threading.Lock()
        self.registry = None
        self.route_profiles: Dict[str, str] = {}

    def bind_registry(self, registry) -> None:
        self.registry = registry

    def set_route_profiles(self, profiles: Dict[str, str]) -> None:
        self.route_profiles = dict(profiles)

    def _tier1(self, route_name: str) -> str:
        if not route_name or route_name not in self.route_profiles:
            return ""
        desc = self.route_profiles[route_name]
        return f"领域画像[{route_name}]：{desc}。请结合该领域知识给出根因分析与解决方案。"

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
        self, project_id: str, session_id: str, route_name: str
    ) -> Tuple[int, str]:
        """四层降级：Tier1/2 命中时合并追加 Tier3 会话摘要（而非丢弃），
        保证多轮会话中"用户已回答澄清问题"的上下文能延续到下一轮。"""
        tier1 = self._tier1(route_name)
        if tier1.strip():
            return self._merge_session(1, tier1, project_id, session_id)

        tier2 = self._tier2(project_id)
        if tier2.strip():
            return self._merge_session(2, tier2, project_id, session_id)

        tier3 = self.sessions.get(self._session_key(project_id, session_id))
        if tier3:
            return 3, tier3

        return 4, ""

    def _merge_session(
        self, tier: int, content: str, project_id: str, session_id: str
    ) -> Tuple[int, str]:
        summary = self.sessions.get(self._session_key(project_id, session_id))
        if not summary:
            return tier, content
        return tier, f"{content}\n\n[会话历史摘要]\n{summary}"

    @staticmethod
    def _session_key(project_id: str, session_id: str) -> str:
        return f"{project_id}::{session_id}"

    def remember(self, project_id: str, session_id: str, summary: str) -> None:
        self.sessions.set(self._session_key(project_id, session_id), summary)

    def mark_clarified(self, project_id: str, session_id: str) -> None:
        """需求澄清模式每会话最多触发一轮，标记后不再触发。"""
        with self._clarified_lock:
            self._clarified.add(self._session_key(project_id, session_id))

    def already_clarified(self, project_id: str, session_id: str) -> bool:
        with self._clarified_lock:
            return self._session_key(project_id, session_id) in self._clarified
