import json
import os
import threading
from pathlib import Path
from typing import Dict, Optional


class ProjectRegistry:
    def __init__(self, store_path: str):
        self.store_path = Path(store_path)
        self._lock = threading.Lock()
        self._data: Dict[str, Dict] = {}
        self._load()

    def _load(self) -> None:
        try:
            if self.store_path.is_file():
                self._data = json.loads(self.store_path.read_text(encoding="utf-8"))
        except Exception:
            self._data = {}

    def _save(self) -> None:
        try:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            self.store_path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    def register(self, project_id: str, agents_path: str, description: str = "") -> Dict:
        with self._lock:
            self._data[project_id] = {
                "agents_path": agents_path,
                "description": description,
                "updated_at": _now(),
            }
            self._save()
            return self._data[project_id]

    def unregister(self, project_id: str) -> bool:
        with self._lock:
            existed = project_id in self._data
            self._data.pop(project_id, None)
            self._save()
            return existed

    def get(self, project_id: str) -> Optional[Dict]:
        with self._lock:
            entry = self._data.get(project_id)
            return dict(entry) if entry else None

    def agents_file(self, project_id: str) -> Optional[Path]:
        entry = self.get(project_id)
        if not entry or not entry.get("agents_path"):
            return None
        p = Path(entry["agents_path"])
        if p.is_file():
            return p
        if p.is_dir():
            candidate = p / "AGENTS.md"
            return candidate if candidate.is_file() else None
        return None

    def all(self) -> Dict:
        with self._lock:
            return dict(self._data)


def _now() -> str:
    import time

    return time.strftime("%Y-%m-%dT%H:%M:%S%z")
