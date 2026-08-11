import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.upstream.llm import UpstreamClient


def test_headers_with_key():
    c = UpstreamClient("https://example.com/v1", "sk-real")
    h = c._headers()
    assert h["Authorization"] == "Bearer sk-real"
    assert h["Content-Type"] == "application/json"


def test_headers_empty_key_no_auth():
    c = UpstreamClient("https://example.com/v1", "")
    assert "Authorization" not in c._headers()


def test_headers_sk_none_no_auth():
    c = UpstreamClient("https://example.com/v1", "sk-none")
    assert "Authorization" not in c._headers()
