"""权限分级拦截层（L0/L1/L2 三级行动权限）。

网关只转发请求不执行操作，本层做的是**指令级危险扫描**：
在请求进入转发前识别用户消息中的危险指令，按权限等级决定
allow / confirm（二次确认） / block（拦截）。

| 等级 | 语义 | 高危指令(rm -rf/DROP/format/shutdown) |
|---|---|---|
| L0 侦察兵 | 只读侦察 | 拦截 |
| L1 执行员 | 读写执行 | 拦截 |
| L2 指挥官 | 全量+高危 | 放行但标记 confirm |

等级从请求头 `x-permission-level` 读取，默认 L1（config.security.default_level）。
"""

import re
from typing import Dict, List, Tuple

PERM_LEVELS = ("L0", "L1", "L2")
_LEVEL_RANK = {"L0": 0, "L1": 1, "L2": 2}

# (pattern, 所需最小等级, 标签)
DANGER_PATTERNS: List[Tuple[re.Pattern, str, str]] = [
    (re.compile(r"\brm\s+(-[a-z]*r[a-z]*f|[a-z]*f[a-z]*r)\b", re.I), "L2", "rm -rf 递归强制删除"),
    (re.compile(r"\bdel\s+/[a-z]*s\b", re.I), "L2", "del /s 递归强制删除"),
    (re.compile(r"\brmdir\s+/[a-z]*s\b", re.I), "L2", "rmdir /s 递归删除"),
    (re.compile(r"\bDROP\s+(DATABASE|TABLE|SCHEMA)\b", re.I), "L2", "DROP 删除数据库对象"),
    (re.compile(r"\bTRUNCATE\s+TABLE\b", re.I), "L2", "TRUNCATE 清空数据表"),
    (re.compile(r"\bDETACH\s+DATABASE\b", re.I), "L2", "DETACH 分离数据库"),
    (re.compile(r"\bformat\s+[a-zA-Z]:", re.I), "L2", "磁盘格式化"),
    (re.compile(r"\bshutdown\b", re.I), "L2", "系统关机"),
    (re.compile(r"\breboot\b", re.I), "L2", "系统重启"),
    (re.compile(r"\bkill\s+-9\b", re.I), "L2", "kill -9 强杀进程"),
    (re.compile(r"\bpkill\s+-9\b", re.I), "L2", "pkill -9 强杀进程"),
    (re.compile(r"\bdiskpart\b", re.I), "L2", "diskpart 磁盘分区操作"),
    (re.compile(r"\breg\s+delete\b", re.I), "L2", "注册表删除"),
    (re.compile(r":\(\)\s*\{\s*[^}]*\}\s*;", re.I), "L2", "fork 炸弹"),
    (re.compile(r"\bdel\s+/[a-z]*q\b", re.I), "L1", "del /q 静默删除"),
    (re.compile(r"\brd\s+/[a-z]*s[a-z]*[qf]\b", re.I), "L1", "rd /s 目录删除"),
    (re.compile(r"\brm\s+-rf\b", re.I), "L2", "rm -rf 递归强制删除"),
]


def scan_danger(text: str) -> List[Dict]:
    """扫描文本中的危险指令，返回 [{"pattern", "level", "label"}]（按出现顺序去重）。"""
    if not text:
        return []
    found: List[Dict] = []
    seen = set()
    for pat, lvl, label in DANGER_PATTERNS:
        if pat.search(text):
            key = pat.pattern
            if key in seen:
                continue
            seen.add(key)
            found.append({"pattern": key, "level": lvl, "label": label})
    return found


def check_permission(text: str, level: str = "L1") -> Dict:
    """按权限等级判定：危险指令命中时决定 allow/confirm/block。"""
    level = (level or "L1").upper()
    if level not in _LEVEL_RANK:
        level = "L1"
    matched = scan_danger(text)
    if not matched:
        return {"allowed": True, "action": "allow", "level": level, "matched": []}

    max_required = max(_LEVEL_RANK[m["level"]] for m in matched)
    required = next(
        (lv for lv in ("L0", "L1", "L2") if _LEVEL_RANK[lv] == max_required), "L2"
    )
    if _LEVEL_RANK[level] < max_required:
        return {
            "allowed": False,
            "action": "block",
            "level": level,
            "required": required,
            "matched": matched,
        }
    return {
        "allowed": True,
        "action": "confirm" if max_required >= _LEVEL_RANK["L2"] else "allow",
        "level": level,
        "required": required,
        "matched": matched,
    }
