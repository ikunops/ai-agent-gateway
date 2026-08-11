import re
from typing import Dict, List, Optional

_WS_RE = re.compile(r"[ \t]+")
_NL_RE = re.compile(r"\r\n|\r")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_TS_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b")
_UUID_RE = re.compile(
    r"\b[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\b"
)


def is_likely_code_block(text: str) -> bool:
    lines = text.split("\n")
    if len(lines) < 2:
        return False
    code_fence = 0
    indented = 0
    for line in lines:
        if line.strip().startswith("```"):
            code_fence += 1
        if line.startswith("    ") or line.startswith("\t"):
            indented += 1
    return code_fence >= 2 or indented / max(len(lines), 1) > 0.3


def _desensitize(text: str) -> str:
    text = _TS_RE.sub("[TIMESTAMP]", text)
    text = _UUID_RE.sub("[UUID]", text)
    return text


def normalize_text(text: str, role: str = "user") -> str:
    if not text:
        return ""
    if is_likely_code_block(text):
        if role == "system":
            return _desensitize(text.strip())
        return text.strip()
    text = _NL_RE.sub("\n", text)
    lines = [_WS_RE.sub(" ", line.strip()) for line in text.split("\n")]
    text = "\n".join(line for line in lines if line)
    text = _CTRL_RE.sub("", text)
    if role == "system":
        text = _desensitize(text)
    return text


def clean_messages(messages: List[Dict]) -> List[Dict]:
    cleaned: List[Dict] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, list):
            cleaned.append({**msg, "role": role})
        elif isinstance(content, str):
            cleaned.append(
                {**msg, "role": role, "content": normalize_text(content, role)}
            )
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
    default_project_id: str = "default",
    default_session_id: str = "default",
) -> SessionInfo:
    headers = headers or {}
    project_id = headers.get("x-project-id", "")
    session_id = headers.get("x-session-id", "")
    user_id = headers.get("x-user-id", "")
    return SessionInfo(
        project_id or default_project_id,
        session_id or default_session_id,
        user_id or "anonymous",
    )


def auth_ok(api_key: str, valid_keys: Dict[str, str]) -> bool:
    if not api_key:
        return False
    return any(valid == api_key for valid in valid_keys.values())
