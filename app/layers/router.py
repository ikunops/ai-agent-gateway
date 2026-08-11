import hashlib
import math
import re
import threading
import time
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

_CACHE_DEFAULT_TTL = 3600
_CACHE_MAX = 1024
_SIM_THRESHOLD = 0.7

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
    ):
        self.profiles = {name: RouteProfile(name, desc) for name, desc in profiles.items()}
        self.threshold = threshold
        self.ttl = cache_ttl
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

    def _vector_match(self, text: str) -> Optional[Tuple[str, float]]:
        if self._fast.available:
            try:
                q = self._fast.embed([text])[0]
                pd = self._fast.embed([p.description for p in self.profiles.values()])
                best_name, best_score = None, -1.0
                for name, pv in zip(self.profiles.keys(), pd):
                    score = _cosine(q, pv)
                    if score > best_score:
                        best_name, best_score = name, score
                if best_name and best_score >= self.threshold:
                    return best_name, best_score
            except Exception:
                pass
        qv = self._tfidf.embed(text)
        best_name, best_score = None, -1.0
        for name, pv in self._profile_vecs:
            score = self._tfidf.similarity(qv, pv)
            if score > best_score:
                best_name, best_score = name, score
        if best_name and best_score >= self.threshold:
            return best_name, best_score
        return None

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
    ) -> Tuple[str, float, str]:
        """返回 (route_name, score, source)。source: cache/vector/llm/fallback"""
        key = hashlib.sha256(text.encode("utf-8")).hexdigest()

        hit = self._cache_get(key)
        if hit:
            return hit[0], hit[1], "cache"

        m = self._vector_match(text)
        if m:
            name, score = m
            self._cache_set(key, name, score, "vector")
            return name, score, "vector"

        if llm_judge is not None:
            name = await llm_judge(text, list(self.profiles.keys()))
            if name and name in self.profiles:
                self._cache_set(key, name, 1.0, "llm")
                return name, 1.0, "llm"

        return "", 0.0, "fallback"


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
