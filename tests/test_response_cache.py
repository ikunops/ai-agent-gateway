import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.layers.response_cache import (
    ResponseCache,
    build_sse_chunks,
    make_session_summary,
    request_cache_key,
    stats_snapshot,
)


def test_request_cache_key_ignores_stream():
    a = request_cache_key(
        b'{"stream": true, "model": "m", "messages": [{"role": "user", "content": "hi"}]}'
    )
    b = request_cache_key(
        b'{"stream": false, "model": "m", "messages": [{"role": "user", "content": "hi"}]}'
    )
    assert a == b


def test_request_cache_key_differs_on_content():
    a = request_cache_key(
        b'{"model": "m", "messages": [{"role": "user", "content": "hi"}]}'
    )
    b = request_cache_key(
        b'{"model": "m", "messages": [{"role": "user", "content": "hello"}]}'
    )
    assert a != b


def test_response_cache_roundtrip(tmp_path):
    rc = ResponseCache(str(tmp_path / "cache"))
    rc.set("abc", {"id": "x", "choices": []})
    assert rc.get("abc") == {"id": "x", "choices": []}
    assert rc.get("nope") is None


def test_response_cache_clear(tmp_path):
    rc = ResponseCache(str(tmp_path / "cache"))
    rc.set("abc", {"id": "x"})
    rc.set("def", {"id": "y"})
    assert rc.clear() == 2
    assert rc.get("abc") is None


def test_build_sse_chunks():
    cached = {
        "id": "cid",
        "model": "m",
        "created": 123,
        "choices": [{"message": {"role": "assistant", "content": "hello world"}}],
    }
    chunks = build_sse_chunks(cached)
    assert chunks[0]["choices"][0]["delta"]["role"] == "assistant"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


def test_session_summary():
    msgs = [{"role": "user", "content": "帮我查 k8s 节点"}]
    s = make_session_summary(msgs, "已检查 3 个节点")
    assert "k8s" in s and "3 个节点" in s


def test_stats_snapshot_has_fields(tmp_path):
    snap = stats_snapshot()
    assert "hit_rate" in snap and "usd_saved" in snap and "uptime_s" in snap
