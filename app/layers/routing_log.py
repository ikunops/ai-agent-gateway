"""路由决策明细账：记录每次路由决策的完整上下文，运行一段时间后据此调优。

落盘：logs/routing/routing-YYYY-MM-DD.jsonl（与审计同周期轮转）
查看：GET /v1/stats/routing 返回聚合摘要：
  - 决策来源分布（cache/term/vector/llm/fallback/clarify-skip）
  - 路由标签分布、Tier 分布
  - 高频触发的技术词（评估词表是否命中真实流量）
  - 平均摘要长度 / 保真闸门挽回段数 / 向量与 LLM 分类延迟
  - 分类器使用情况（by_classifier）：每个分类器被调用次数、成功/失败、
    平均延迟、投票分布与生效 stage —— 据此评估各分类器取舍
"""

import json
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Dict, List

_AVG_FIELDS = ("digest_len", "fidelity_forced", "vector_latency_ms", "judge_latency_ms")


class RoutingLedger:
    def __init__(self, directory: str, keep_days: int = 30):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.keep_days = keep_days
        self._lock = threading.Lock()

    def log(self, record: Dict) -> None:
        with self._lock:
            fname = time.strftime("routing-%Y-%m-%d.jsonl", time.localtime())
            f = self.dir / fname
            with open(f, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._prune()

    def _prune(self) -> None:
        if self.keep_days <= 0:
            return
        cutoff = time.time() - self.keep_days * 86400
        for f in self.dir.glob("routing-*.jsonl"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass

    def summarize(self, max_records: int = 200000) -> Dict:
        by_source: Dict[str, int] = {}
        by_route: Dict[str, int] = {}
        by_tier: Dict[str, int] = {}
        terms_counter: Counter = Counter()
        cls_stats: Dict[str, Dict] = {}
        n = 0
        clarify = 0
        sums: Dict[str, float] = {k: 0.0 for k in _AVG_FIELDS}
        counts: Dict[str, int] = {k: 0 for k in _AVG_FIELDS}
        score_sum = 0.0
        for f in sorted(self.dir.glob("routing-*.jsonl")):
            if n >= max_records:
                break
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    for line in fh:
                        if n >= max_records:
                            break
                        try:
                            rec = json.loads(line)
                        except Exception:
                            continue
                        n += 1
                        source = rec.get("source") or "?"
                        by_source[source] = by_source.get(source, 0) + 1
                        route = rec.get("route") or "(none)"
                        by_route[route] = by_route.get(route, 0) + 1
                        tier = str(rec.get("tier", "?"))
                        by_tier[tier] = by_tier.get(tier, 0) + 1
                        for t in rec.get("terms") or []:
                            terms_counter[t] += 1
                        self._aggregate_classifier(rec, cls_stats)
                        if rec.get("clarify"):
                            clarify += 1
                        for k in sums:
                            v = rec.get(k)
                            if v is not None:
                                sums[k] += float(v)
                                counts[k] += 1
                        score_sum += float(rec.get("score") or 0)
            except OSError:
                continue
        return {
            "records": n,
            "by_source": by_source,
            "by_route": by_route,
            "by_tier": by_tier,
            "clarify": clarify,
            "top_terms": [t for t, _c in terms_counter.most_common(20)],
            "by_classifier": self._finalize_classifier(cls_stats),
            "avg_score": round(score_sum / n, 3) if n else 0.0,
            **{
                f"avg_{k}": round(sums[k] / counts[k], 2) if counts[k] else 0.0
                for k in _AVG_FIELDS
            },
        }

    @staticmethod
    def _aggregate_classifier(rec: Dict, acc: Dict[str, Dict]) -> None:
        """按分类器聚合使用情况：调用次数 / 成功失败 / 延迟 / 投票与生效 stage。"""
        calls = rec.get("classifier_calls") or []
        if not calls:
            return
        stage = rec.get("classifier_stage") or ""
        route = rec.get("route") or "(none)"
        for call in calls:
            name = call.get("name")
            if not name:
                continue
            bucket = acc.setdefault(name, {
                "calls": 0, "ok": 0, "failed": 0,
                "latency_ms": 0, "vote_ms": 0,
                "votes": {}, "stages": {},
            })
            bucket["calls"] += 1
            ok = bool(call.get("ok"))
            if ok:
                bucket["ok"] += 1
                bucket["votes"][call.get("vote") or route] = bucket["votes"].get(call.get("vote") or route, 0) + 1
                bucket["vote_ms"] += int(call.get("latency_ms") or 0)
            else:
                bucket["failed"] += 1
                bucket["latency_ms"] += int(call.get("latency_ms") or 0)
            bucket["stages"][stage] = bucket["stages"].get(stage, 0) + 1

    @staticmethod
    def _finalize_classifier(acc: Dict[str, Dict]) -> Dict[str, Dict]:
        out: Dict[str, Dict] = {}
        for name, b in acc.items():
            ok = b["ok"]
            out[name] = {
                "calls": b["calls"],
                "ok": ok,
                "failed": b["failed"],
                "fail_rate": round(b["failed"] / b["calls"], 3) if b["calls"] else 0.0,
                "avg_latency_ms": round((b["latency_ms"] + b["vote_ms"]) / b["calls"], 1) if b["calls"] else 0.0,
                "votes": b["votes"],
                "stages": b["stages"],
            }
        return out
