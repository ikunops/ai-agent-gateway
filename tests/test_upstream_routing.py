import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import Config, UpstreamConfig, UpstreamRoute
from app.main import create_app


class MockA(BaseHTTPRequestHandler):
    calls = 0
    last_model = None

    def do_POST(self):
        type(self).calls += 1
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).last_model = body.get("model")
        payload = json.dumps(
            {"id": "a", "model": body.get("model"),
             "choices": [{"message": {"role": "assistant", "content": "A"}}],
             "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}},
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class MockB(BaseHTTPRequestHandler):
    calls = 0
    last_model = None

    def do_POST(self):
        type(self).calls += 1
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).last_model = body.get("model")
        payload = json.dumps(
            {"id": "b", "model": body.get("model"),
             "choices": [{"message": {"role": "assistant", "content": "B"}}],
             "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}},
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class MockC(BaseHTTPRequestHandler):
    """专用路由分类器 mock：只回 'generic'，避免污染转发计数断言。"""
    calls = 0

    def do_POST(self):
        type(self).calls += 1
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        payload = b'"generic"'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def two_upstreams():
    MockA.calls = 0
    MockB.calls = 0
    MockC.calls = 0
    sa = HTTPServer(("127.0.0.1", 0), MockA)
    sb = HTTPServer(("127.0.0.1", 0), MockB)
    sc = HTTPServer(("127.0.0.1", 0), MockC)
    ta = threading.Thread(target=sa.serve_forever, daemon=True)
    tb = threading.Thread(target=sb.serve_forever, daemon=True)
    tc = threading.Thread(target=sc.serve_forever, daemon=True)
    ta.start()
    tb.start()
    tc.start()
    yield (
        f"http://127.0.0.1:{sa.server_port}/v1",
        f"http://127.0.0.1:{sb.server_port}/v1",
        f"http://127.0.0.1:{sc.server_port}/v1",
    )
    sa.shutdown()
    sb.shutdown()
    sc.shutdown()


@pytest.fixture()
def client(two_upstreams, tmp_path):
    url_a, url_b, url_c = two_upstreams
    cfg = Config(
        api_keys={"default": "test-key"},
        upstream=UpstreamConfig(base_url="", api_key_env="", default_model="free-1"),
        upstream_routes=[
            UpstreamRoute(
                name="pool-a", base_url=url_a, api_key_env="",
                default_model="free-1", match_suffix="-free",
            ),
            UpstreamRoute(
                name="pool-b", base_url=url_b, api_key_env="",
                default_model="go-1", match_models=["glm-5.2", "kimi-k3"],
            ),
        ],
        classifiers=[{"name": "mockc", "base_url": url_c, "model": "mockc-1", "local": True}],
        routes={"generic": "通用问题"},
        audit_dir=str(tmp_path / "logs"),
        data_dir=str(tmp_path / "data"),
    )
    return TestClient(create_app(cfg))


def test_route_by_suffix(client):
    r = client.post(
        "/v1/chat/completions",
        headers={"X-API-Key": "test-key"},
        json={"messages": [{"role": "user", "content": "hi"}], "model": "deepseek-free", "stream": False},
    )
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == "A"
    assert MockA.last_model == "deepseek-free"


def test_route_by_exact_model(client):
    r = client.post(
        "/v1/chat/completions",
        headers={"X-API-Key": "test-key"},
        json={"messages": [{"role": "user", "content": "hi"}], "model": "glm-5.2", "stream": False},
    )
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == "B"
    assert MockB.last_model == "glm-5.2"


def test_route_exact_beats_suffix(client):
    """glm-5.2 同时命中 pool-a 的 -free 后缀与 pool-b 的精确列表时，精确优先。"""
    MockA.calls = 0
    MockB.calls = 0
    r = client.post(
        "/v1/chat/completions",
        headers={"X-API-Key": "test-key"},
        json={"messages": [{"role": "user", "content": "hi"}], "model": "glm-5.2", "stream": False},
    )
    assert r.status_code == 200
    assert MockB.calls == 1 and MockA.calls == 0


def test_unknown_model_no_default_returns_400(client):
    r = client.post(
        "/v1/chat/completions",
        headers={"X-API-Key": "test-key"},
        json={"messages": [{"role": "user", "content": "hi"}], "model": "nope", "stream": False},
    )
    assert r.status_code == 400
    assert "未匹配" in r.json()["detail"]


def test_unknown_model_falls_back_to_default(tmp_path, two_upstreams):
    url_a, url_b, url_c = two_upstreams
    cfg = Config(
        api_keys={"default": "test-key"},
        upstream=UpstreamConfig(base_url=url_a, api_key_env="", default_model="free-1"),
        upstream_routes=[
            UpstreamRoute(
                name="pool-b", base_url=url_b, api_key_env="",
                default_model="go-1", match_models=["glm-5.2"],
            ),
        ],
        classifiers=[{"name": "mockc", "base_url": url_c, "model": "mockc-1", "local": True}],
        routes={"generic": "通用问题"},
        audit_dir=str(tmp_path / "logs"),
        data_dir=str(tmp_path / "data"),
    )
    c = TestClient(create_app(cfg))
    r = c.post(
        "/v1/chat/completions",
        headers={"X-API-Key": "test-key"},
        json={"messages": [{"role": "user", "content": "hi"}], "model": "nope", "stream": False},
    )
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == "A"


def test_no_upstream_returns_400(client, two_upstreams, tmp_path):
    cfg = Config(
        api_keys={"default": "test-key"},
        upstream=UpstreamConfig(base_url="", api_key_env="", default_model=""),
        routes={"generic": "通用问题"},
        audit_dir=str(tmp_path / "logs"),
        data_dir=str(tmp_path / "data"),
    )
    c = TestClient(create_app(cfg))
    r = c.post(
        "/v1/chat/completions",
        headers={"X-API-Key": "test-key"},
        json={"messages": [{"role": "user", "content": "hi"}], "stream": False},
    )
    assert r.status_code == 400
    assert "纯整形" in r.json()["detail"]
