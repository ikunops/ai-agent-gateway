import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.layers.cache import CacheEngine
from app.layers.preprocess import SessionInfo, auth_ok
from app.layers.stats import Stats


def test_auth():
    assert auth_ok("k1", {"a": "k1"})
    assert not auth_ok("bad", {"a": "k1"})
    assert not auth_ok("", {"a": "k1"})


def test_cache_tier_fallback():
    cache = CacheEngine(project_root=None, max_sessions=10)
    cache.set_route_profiles({"java": "处理JVM内存模型、并发编程、Maven/Gradle构建"})
    tier, content = cache.resolve("proj", "sess", "java")
    assert tier == 1 and "java" in content
    tier, content = cache.resolve("proj", "sess", "")
    assert tier == 4 and content == ""


def test_cache_tier2_file(tmp_path):
    (tmp_path / "AGENTS.md").write_text("项目规范: 禁止删除Pod", encoding="utf-8")
    cache = CacheEngine(project_root=tmp_path, max_sessions=10)
    tier, content = cache.resolve("proj", "sess", "")
    assert tier == 2 and "禁止删除Pod" in content


def test_tier3_session_remember():
    cache = CacheEngine(project_root=None, max_sessions=10)
    cache.remember("p", "s", "历史摘要")
    tier, content = cache.resolve("p", "s", "")
    assert tier == 3 and "历史摘要" in content


def test_stats_prefix_overlap(tmp_path):
    stats = Stats(str(tmp_path), 30)
    a = "A" * 100 + "B" * 50
    b = "A" * 100 + "C" * 50
    assert stats.record_prefix("k", a) == 0
    overlap = stats.record_prefix("k", b)
    assert overlap == 100
