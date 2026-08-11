import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.layers.routing_log import RoutingLedger


def test_routing_ledger_roundtrip(tmp_path):
    ledger = RoutingLedger(str(tmp_path))
    ledger.log({"source": "term", "route": "mysql", "tier": 1, "terms": ["mysql"], "score": 0.95})
    ledger.log({"source": "llm", "route": "kubernetes", "tier": 1, "terms": [], "score": 1.0,
                "digest_len": 320, "fidelity_forced": 2, "vector_latency_ms": 15, "judge_latency_ms": 280})
    ledger.log({"source": "clarify-skip", "route": "", "tier": 4, "terms": [], "clarify": True})
    s = ledger.summarize()
    assert s["records"] == 3
    assert s["by_source"] == {"term": 1, "llm": 1, "clarify-skip": 1}
    assert s["by_route"]["mysql"] == 1
    assert s["by_tier"]["1"] == 2
    assert s["clarify"] == 1
    assert "mysql" in s["top_terms"]
    assert s["avg_digest_len"] == 320
    assert s["avg_fidelity_forced"] == 2
    assert s["avg_judge_latency_ms"] == 280


def test_routing_ledger_empty(tmp_path):
    s = RoutingLedger(str(tmp_path)).summarize()
    assert s["records"] == 0
    assert s["by_source"] == {}
    assert s["avg_score"] == 0.0
