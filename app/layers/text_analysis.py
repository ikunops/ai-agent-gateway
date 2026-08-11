"""长文本分段 / 技术词提取 / 路由摘要。

供 L2 路由（快路径 + 逐段向量）与模糊需求检测共享同一张词表。
"""

import re
from typing import Dict, List, Optional

# 技术栈词表（路由快路径真源 + 模糊需求拦截词）。
# 注意：只收"技术栈"词，不收平台词（手机/桌面/网页）——
# 平台正是需求澄清要问的目标，收进来会永远不触发澄清。
TECH_TERMS: List[str] = [
    "android", "ios", "java", "python", "go", "golang", "javascript", "typescript",
    "js", "ts", "rust", "c++", "cpp", "c#", "php", "ruby", "swift", "kotlin",
    "flutter", "react", "vue", "angular", "nextjs", "web", "app", "docker",
    "k8s", "kubernetes", "sql", "mysql", "oracle", "postgres", "postgresql",
    "redis", "kafka", "spring", "django", "fastapi", "flask", "linux", "git",
    "nginx", "mongodb", "elasticsearch", "grafana", "prometheus", "terraform",
    "ansible", "helm",
]

# 技术词 → 路由标签别名（如 k8s → kubernetes）
TERM_ROUTE_ALIASES: Dict[str, str] = {
    "go": "go", "golang": "go",
    "k8s": "kubernetes", "kubernetes": "kubernetes",
    "mysql": "mysql", "oracle": "oracle",
    "postgres": "postgres", "postgresql": "postgres",
    "mongodb": "mongodb", "redis": "redis", "kafka": "kafka",
    "linux": "linux", "docker": "docker", "nginx": "nginx",
    "git": "git", "java": "java", "python": "python", "js": "js",
    "ts": "ts", "rust": "rust", "php": "php", "swift": "swift",
    "kotlin": "kotlin", "flutter": "flutter", "react": "react", "vue": "vue",
    "spring": "java", "fastapi": "python", "flask": "python", "django": "python",
    "terraform": "terraform", "ansible": "ansible", "helm": "kubernetes",
}

# 开发意图动词（模糊需求触发词）
DEV_VERBS: List[str] = [
    "开发", "做一个", "做个", "做", "帮我写", "帮我做", "写一个", "搭一个",
    "实现", "想要一个", "搞一个", "整一个", "建一个",
]

# 否定守卫（动词前 4 字符内出现则不算开发意图）
NEGATION_WORDS: List[str] = ["不需要", "不用", "别", "不要", "不是", "拒绝", "无需", "莫"]

# 寒暄/废话段（路由与摘要过滤用）
FILLER_WORDS: List[str] = [
    "你好", "您好", "嗨", "hi", "hello", "谢谢", "感谢", "麻烦了", "辛苦了",
    "帮帮忙", "好的", "可以", "嗯", "ok", "打扰", "请问", "帮忙",
]

# 模糊需求最大长度（超出视为已自带规格，不触发澄清）
VAGUE_MAX_CHARS = 60

# 保真闸门：摘要中强制保留的原文段特征。
# 否定词/连接词/关键符号被摘掉会导致语义反转或范围变化（"不用 MySQL 改用 Oracle"），
# 凡含以下特征的段，无论模型选没选，都必须并入摘要。
_FIDELITY_RE = re.compile(
    r"不|没|别|非|勿|禁|拒|不要|不能|不会|没有|无需|未|"
    r"和|与|或者|或|以及|并且|但是|但|不过|虽然|即使|除非|"
    r"&&|\|\||=>|::|!=|==|>=|<=|"
    r"\bnot\b|\band\b|\bor\b|\bbut\b",
    re.I,
)

_TERM_RE_CACHE: Dict[str, re.Pattern] = {}


def _term_re(term: str) -> re.Pattern:
    pat = _TERM_RE_CACHE.get(term)
    if pat is None:
        pat = re.compile(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])")
        _TERM_RE_CACHE[term] = pat
    return pat


def extract_tech_terms(text: str, terms: Optional[List[str]] = None) -> List[str]:
    """跨全文提取技术词（拉丁词带词边界，避免 apple 命中 app）。"""
    if not text:
        return []
    lower = text.lower()
    found: List[str] = []
    for t in sorted(terms or TECH_TERMS, key=len, reverse=True):
        if _term_re(t).search(lower):
            found.append(t)
    return found


def split_segments(text: str, min_len: int = 2) -> List[str]:
    """按换行切段，过滤寒暄/废话段；无换行的超长段按句子/定长再切。

    复制粘贴的墙式文本（无换行）会退化为单段 → 向量信号稀释、摘要丢尾部，
    因此对超过 _LONG_SEGMENT_CHARS 的单段做二次切分。
    """
    if not text or not text.strip():
        return []
    parts = re.split(r"\n\s*\n|\n", text)
    segs: List[str] = []
    for p in parts:
        p = p.strip()
        if len(p) < min_len:
            continue
        segs.extend(_split_long_segment(p))
    kept = [s for s in segs if not _is_filler(s)]
    return kept or segs


_LONG_SEGMENT_CHARS = 300
_SENTENCE_RE = re.compile(r"[。！？；!?;]")


def _split_long_segment(seg: str, max_chars: int = _LONG_SEGMENT_CHARS) -> List[str]:
    """超长单段二次切分：优先按句子切后合并成 <=max_chars 的块；
    无句子边界（纯日志/代码）则按定长切块。"""
    if len(seg) <= max_chars:
        return [seg]
    pieces = [p.strip() for p in _SENTENCE_RE.split(seg) if p.strip()]
    if len(pieces) <= 1:
        return [seg[i : i + max_chars] for i in range(0, len(seg), max_chars)]
    out: List[str] = []
    buf = ""
    for p in pieces:
        if len(p) > max_chars:
            if buf:
                out.append(buf)
                buf = ""
            out.extend(_split_long_segment(p, max_chars))
            continue
        if not buf:
            buf = p
        elif len(buf) + len(p) + 1 <= max_chars:
            buf = buf + "。" + p
        else:
            out.append(buf)
            buf = p
    if buf:
        out.append(buf)
    return out


def _is_filler(text: str) -> bool:
    s = text.strip().rstrip("，。,.!！;；:：")
    if not s:
        return True
    return any(s.lower() == w.lower() for w in FILLER_WORDS)


def build_routing_digest(
    text: str,
    max_chars: int = 500,
    picked_segments: Optional[List[str]] = None,
    best_segment: str = "",
) -> str:
    """路由/分类输入摘要。短文本原样透传；超长文本取 技术词段 + 最佳匹配段 + 首段 + 末段。

    picked_segments 来自外部抽取模型（只允许选原文段、禁止改写）：
    - 逐字校验：非原文段直接丢弃（防幻觉）
    - 保真闸门：否定词/连接词/关键符号段强制并入（防歧义丢失）
    - 校验失败或无抽取结果 → 回退确定性路径
    best_segment 为向量匹配得分最高的段（意图可能在中段且无技术词，须并入）。
    """
    digest, _ = build_routing_digest_detailed(
        text, max_chars, picked_segments, best_segment
    )
    return digest


def build_routing_digest_detailed(
    text: str,
    max_chars: int = 500,
    picked_segments: Optional[List[str]] = None,
    best_segment: str = "",
) -> tuple[str, int]:
    """返回 (digest, fidelity_forced_count)，供路由观测记录保真闸门挽回了多少段。"""
    if not text:
        return "", 0
    if len(text) <= max_chars:
        return text, 0
    segs = split_segments(text)
    if not segs:
        return text[:max_chars], 0
    if picked_segments:
        valid = [s for s in picked_segments if s in segs]
        if valid:
            merged, forced = apply_fidelity_guards(valid, segs)
            return _join_digest(merged, max_chars), forced
    terms = extract_tech_terms(text)
    term_segs = [s for s in segs if any(_term_re(t).search(s.lower()) for t in terms)]
    keep: List[str] = []
    keep.extend(term_segs)
    if best_segment and best_segment in segs and best_segment not in keep:
        keep.append(best_segment)
    if segs and segs[0] not in keep:
        keep.append(segs[0])
    if len(segs) > 1 and segs[-1] not in keep:
        keep.append(segs[-1])
    merged, forced = apply_fidelity_guards(keep, segs)
    return _join_digest(merged, max_chars), forced


def apply_fidelity_guards(
    selected: List[str], all_segs: List[str]
) -> tuple[List[str], int]:
    """保真闸门：把含否定词/连接词/关键符号的原文段强制并入选定段，返回（合并结果，强制段数）。"""
    forced = [s for s in all_segs if _FIDELITY_RE.search(s) and s not in selected]
    return selected + forced, len(forced)


def _join_digest(segments: List[str], max_chars: int) -> str:
    digest = "\n\n".join(dict.fromkeys(segments))
    if len(digest) > max_chars:
        digest = digest[:max_chars].rstrip() + "…"
    return digest


def route_name_from_terms(text: str, profiles: Dict[str, str]) -> Optional[str]:
    """快路径：技术词直接命中路由标签 → 返回路由名（O(词表) 成本，零 LLM）。"""
    for t in extract_tech_terms(text):
        name = TERM_ROUTE_ALIASES.get(t)
        if name and name in profiles:
            return name
    return None
