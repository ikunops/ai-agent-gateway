import hashlib
import math
import re
import threading
import time
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

from app.layers.text_analysis import (
    build_routing_digest,
    build_routing_digest_detailed,
    extract_tech_terms,
    route_name_from_terms,
    split_segments,
)

_CACHE_DEFAULT_TTL = 3600
_CACHE_MAX = 1024
_SIM_THRESHOLD = 0.7
_MAX_SEGMENTS = 8

_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]|[a-zA-Z0-9]+")


def _tokens(text: str) -> List[str]:
    text = text.lower()
    chars = _TOKEN_RE.findall(text)
    return chars


def _ngrams(tokens: List[str], n: int = 2) -> List[str]:
    out: List[str] = []
    joined = "".join(tokens)
    if len(joined) < n:
        return [joined] if joined else []
    for i in range(len(joined) - n + 1):
        out.append(joined[i : i + n])
    return out


class TfidfEmbedder:
    """零依赖字符 n-gram TF-IDF 向量。降级方案；语义质量低于真 embedding。"""

    def __init__(self):
        self._vocab: Dict[str, int] = {}
        self._idf: Dict[str, float] = {}
        self._docs: List[Dict[str, float]] = []
        self._ready = False

    def fit(self, texts: List[str]) -> None:
        n_docs = len(texts)
        df: Dict[str, int] = {}
        docs: List[Dict[str, float]] = []
        for text in texts:
            toks = _ngrams(_tokens(text))
            tf: Dict[str, float] = {}
            for t in toks:
                tf[t] = tf.get(t, 0.0) + 1
            n = sum(tf.values()) or 1
            tf = {k: v / n for k, v in tf.items()}
            for t in tf:
                df[t] = df.get(t, 0) + 1
            docs.append(tf)
        self._idf = {t: math.log((n_docs + 1) / (c + 1)) + 1 for t, c in df.items()}
        self._docs = docs
        self._ready = True

    def embed(self, text: str) -> Dict[str, float]:
        toks = _ngrams(_tokens(text))
        tf: Dict[str, float] = {}
        for t in toks:
            tf[t] = tf.get(t, 0.0) + 1
        n = sum(tf.values()) or 1
        vec: Dict[str, float] = {}
        for t, c in tf.items():
            if t in self._idf:
                vec[t] = (c / n) * self._idf[t]
        return vec

    def similarity(self, a: Dict[str, float], b: Dict[str, float]) -> float:
        if not a or not b:
            return 0.0
        dot = 0.0
        for k, v in a.items():
            if k in b:
                dot += v * b[k]
        na = math.sqrt(sum(v * v for v in a.values()))
        nb = math.sqrt(sum(v * v for v in b.values()))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)


class FastEmbedder:
    """真实语义向量（fastembed + ONNX）。失败时返回 None，由调用方降级。"""

    def __init__(self):
        self._model = None
        self._error: Optional[str] = None
        try:
            from fastembed import TextEmbedding  # type: ignore

            self._model = TextEmbedding(model_name="BAAI/bge-small-zh-v1.5")
        except Exception as e:
            self._error = str(e)

    @property
    def available(self) -> bool:
        return self._model is not None

    @property
    def error(self) -> Optional[str]:
        return self._error

    def embed(self, texts: List[str]) -> List[List[float]]:
        if not self._model:
            return []
        return [list(v) for v in self._model.embed(texts)]


class RouteProfile:
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description


class SemanticRouter:
    """三路决策：快=缓存 中=向量 慢=LLM。不穷举关键词。"""

    def __init__(
        self,
        profiles: Dict[str, str],
        threshold: float = _SIM_THRESHOLD,
        cache_ttl: int = _CACHE_DEFAULT_TTL,
        picker=None,
    ):
        self.profiles = {name: RouteProfile(name, desc) for name, desc in profiles.items()}
        self.threshold = threshold
        self.ttl = cache_ttl
        self.picker = picker
        self._cache: "OrderedDict[str, Tuple[str, float, str, float]]" = OrderedDict()
        self._lock = threading.Lock()
        self._tfidf = TfidfEmbedder()
        self._fast = FastEmbedder()
        self._profile_vecs: List[Tuple[str, Dict[str, float]]] = []
        self._init_vectors()

    def _init_vectors(self) -> None:
        names = list(self.profiles.keys())
        descs = [self.profiles[n].description for n in names]
        self._tfidf.fit(descs)
        self._profile_vecs = [(n, self._tfidf.embed(d)) for n, d in zip(names, descs)]

    def _vector_match(
        self, text: str
    ) -> Tuple[Optional[str], float, str, Dict[str, float]]:
        """返回 (最佳画像名(可 None), 最佳得分, 最佳段, {画像: 最高分段得分})。
        阈值判断交给调用方，最佳段与得分表始终返回（供摘要与观测）。"""
        segs = split_segments(text)
        if len(segs) > _MAX_SEGMENTS:
            segs = segs[:_MAX_SEGMENTS // 2] + segs[-(_MAX_SEGMENTS // 2):]
        scores: Dict[str, float] = {}
        best_name: Optional[str] = None
        best_score, best_seg = -1.0, ""
        if self._fast.available:
            try:
                qs = self._fast.embed(segs)
                pd = self._fast.embed([p.description for p in self.profiles.values()])
                names = list(self.profiles.keys())
                for q, seg in zip(qs, segs):
                    for name, pv in zip(names, pd):
                        score = _cosine(q, pv)
                        if score > scores.get(name, -1.0):
                            scores[name] = score
                        if score > best_score:
                            best_name, best_score, best_seg = name, score, seg
                return best_name, best_score, best_seg, scores
            except Exception:
                pass
        for seg in segs:
            qv = self._tfidf.embed(seg)
            for name, pv in self._profile_vecs:
                score = self._tfidf.similarity(qv, pv)
                if score > scores.get(name, -1.0):
                    scores[name] = score
                if score > best_score:
                    best_name, best_score, best_seg = name, score, seg
        return best_name, best_score, best_seg, scores

    def _cache_get(self, key: str) -> Optional[Tuple[str, float, str, float]]:
        with self._lock:
            if key not in self._cache:
                return None
            val = self._cache[key]
            if time.time() - val[3] > self.ttl:
                del self._cache[key]
                return None
            self._cache.move_to_end(key)
            return val

    def _cache_set(self, key: str, name: str, score: float, source: str) -> None:
        with self._lock:
            self._cache[key] = (name, score, source, time.time())
            self._cache.move_to_end(key)
            while len(self._cache) > _CACHE_MAX:
                self._cache.popitem(last=False)

    async def route(
        self,
        text: str,
        llm_judge=None,
        context: str = "",
    ) -> Tuple[str, float, str]:
        """返回 (route_name, score, source)。source: cache/term/vector/llm/fallback"""
        name, score, source, _meta = await self.route_detailed(
            text, llm_judge=llm_judge, context=context
        )
        return name, score, source

    async def route_detailed(
        self,
        text: str,
        llm_judge=None,
        context: str = "",
    ) -> Tuple[str, float, str, Dict]:
        """同 route()，额外返回决策明细 meta（供路由观测记录）：
        terms / segments / vector_scores / best_segment / digest_len /
        fidelity_forced / vector_latency_ms / judge_latency_ms"""
        key_src = text
        key = hashlib.sha256(key_src.encode("utf-8")).hexdigest()

        terms = extract_tech_terms(text)
        meta: Dict = {"terms": terms, "segments": len(split_segments(text))}

        hit = self._cache_get(key)
        if hit:
            meta.update({"source": "cache"})
            return hit[0], hit[1], "cache", meta

        term_name = route_name_from_terms(text, self.profiles)
        if term_name:
            self._cache_set(key, term_name, 0.95, "term")
            meta.update({"source": "term"})
            return term_name, 0.95, "term", meta

        t0 = time.time()
        best_name, best_score, best_seg, scores = self._vector_match(text)
        vec_ms = int((time.time() - t0) * 1000)
        meta.update({
            "vector_latency_ms": vec_ms,
            "vector_scores": _top_scores(scores),
            "best_segment": best_seg[:100],
        })
        if best_name and best_score >= self.threshold:
            self._cache_set(key, best_name, best_score, "vector")
            meta.update({"source": "vector"})
            return best_name, best_score, "vector", meta

        if llm_judge is not None:
            t1 = time.time()
            digest, forced = build_routing_digest_detailed(text, best_segment=best_seg)
            judge_fn = getattr(llm_judge, "judge_detailed", None) or llm_judge
            result = await judge_fn(digest, list(self.profiles.keys()), context=context)
            judge_ms = int((time.time() - t1) * 1000)
            if isinstance(result, tuple):
                name, vote_meta = result
            else:
                name, vote_meta = result, {}
            meta.update({
                "judge_latency_ms": judge_ms,
                "digest_len": len(digest),
                "fidelity_forced": forced,
            })
            if vote_meta:
                meta["vote"] = vote_meta
            if name and name in self.profiles:
                self._cache_set(key, name, 1.0, "llm")
                meta.update({"source": "llm"})
                score = vote_meta.get("agreement", 1.0) if vote_meta else 1.0
                return name, score, "llm", meta

        meta.update({"source": "fallback"})
        return "", 0.0, "fallback", meta

    async def _digest_for_judge(self, text: str) -> str:
        """本地抽取模型选段 → 保真闸门 → 幻觉校验；失败回退确定性摘要。"""
        if self.picker is not None and len(text) > 500:
            try:
                picked = await self.picker.pick(text)
            except Exception:
                picked = None
            if picked:
                digest = build_routing_digest(text, picked_segments=picked)
                if digest != text:
                    return digest
        return build_routing_digest(text)


def _top_scores(scores: Dict[str, float], k: int = 3) -> Dict[str, float]:
    top = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:k]
    return {name: round(score, 3) for name, score in top}


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
