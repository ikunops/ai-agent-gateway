import re
from typing import Dict, List

_TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
)
_RANDOM_RE = re.compile(
    r"(?i)\b(?:[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})\b"
)
_DATE_RE = re.compile(r"(?<!\d)\d{4}[-/]\d{1,2}[-/]\d{1,2}(?!\d)")
_UUID_RE = re.compile(
    r"(?i)\b[0-9a-f]{32}\b|\b[0-9a-f]{16}\b"
)
_NUMBER_RE = re.compile(r"\b\d{6,}\b")


def extract_dynamic_impurities(text: str) -> tuple[str, List[str]]:
    found: List[str] = []

    def _grab(pattern: re.Pattern) -> None:
        for m in pattern.finditer(text):
            found.append(m.group(0))

    _grab(_TIMESTAMP_RE)
    _grab(_UUID_RE)
    _grab(_DATE_RE)
    _grab(_RANDOM_RE)
    _grab(_NUMBER_RE)
    return text, found


def build_system(
    anchor_prompt: str,
    project_context: str,
    session_context: str,
    routing_note: str,
) -> str:
    parts: List[str] = []
    if anchor_prompt.strip():
        parts.append(anchor_prompt.strip())
    if project_context.strip():
        parts.append(project_context.strip())
    if session_context.strip():
        parts.append(session_context.strip())
    if routing_note.strip():
        parts.append(routing_note.strip())
    return "\n\n".join(parts)


def _split_system_messages(messages: List[Dict]) -> tuple[List[Dict], str, List[Dict]]:
    system_parts: List[str] = []
    others: List[Dict] = []
    for msg in messages:
        if msg.get("role") == "system":
            c = msg.get("content", "")
            if isinstance(c, str):
                system_parts.append(c)
        else:
            others.append(msg)
    return others, "\n\n".join(system_parts), others


def reorganize_messages(
    messages: List[Dict],
    anchor_prompt: str,
    project_context: str = "",
    session_context: str = "",
    routing_note: str = "",
) -> List[Dict]:
    if not messages:
        return []

    system_parts: List[str] = []
    rest: List[Dict] = []
    for msg in messages:
        if msg.get("role") == "system":
            c = msg.get("content", "")
            if isinstance(c, str) and c.strip():
                system_parts.append(c)
        else:
            rest.append(msg)

    if not rest:
        rest = messages

    user_msg = None
    user_idx = -1
    for i in range(len(rest) - 1, -1, -1):
        if rest[i].get("role") == "user" and isinstance(rest[i].get("content"), str):
            user_msg = rest[i]
            user_idx = i
            break

    if user_msg is not None:
        text = user_msg.get("content", "")
        _, impurities = extract_dynamic_impurities(text)
        if impurities:
            rest[user_idx] = {**user_msg, "content": text}
            tail = f"\n[动态信息: {' '.join(impurities)}]"
            rest = rest[: user_idx + 1] + [{"role": "user", "content": tail}] + rest[user_idx + 1 :]

    gateway_system = build_system(
        anchor_prompt, project_context, session_context, routing_note
    )
    full_system = gateway_system
    if system_parts:
        full_system = "\n\n".join([gateway_system, "\n\n".join(system_parts)])

    result: List[Dict] = [{"role": "system", "content": full_system}]
    result.extend(rest)
    return result


def prefix_overlap(a: str, b: str) -> int:
    i = 0
    while i < min(len(a), len(b)) and a[i] == b[i]:
        i += 1
    return i
