import re
from typing import Dict, List, Optional

_WS_RE = re.compile(r"[ \t]+")
_NL_RE = re.compile(r"\r\n|\r")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def normalize_text(text: str) -> str:
    text = _NL_RE.sub("\n", text)
    lines = [_WS_RE.sub(" ", line.strip()) for line in text.split("\n")]
    text = "\n".join(line for line in lines if line)
    return _CTRL_RE.sub("", text)


def clean_messages(messages: List[Dict]) -> List[Dict]:
    cleaned: List[Dict] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, list):
            cleaned.append({**msg, "role": role})
        elif isinstance(content, str):
            cleaned.append({**msg, "role": role, "content": normalize_text(content)})
        else:
            cleaned.append(msg)
    return cleaned


class SessionInfo:
    def __init__(self, project_id: str = "", session_id: str = "", user_id: str = ""):
        self.project_id = project_id or "default"
        self.session_id = session_id or "default"
        self.user_id = user_id or "anonymous"


def parse_session(
    headers: Optional[Dict[str, str]],
    messages: List[Dict],
) -> SessionInfo:
    headers = headers or {}
    project_id = headers.get("x-project-id", "")
    session_id = headers.get("x-session-id", "")
    user_id = headers.get("x-user-id", "")
    return SessionInfo(project_id, session_id, user_id)


def auth_ok(api_key: str, valid_keys: Dict[str, str]) -> bool:
    if not api_key:
        return False
    return any(valid == api_key for valid in valid_keys.values())
