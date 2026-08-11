import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import Config, UpstreamConfig
from app.main import create_app


class MockUpstream(BaseHTTPRequestHandler):
    calls = 0

    def do_POST(self):
        type(self).calls += 1
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) or b"{}"
        body = json.loads(raw)
        stream = bool(body.get("stream", False))
        model = body.get("model", "unknown")
        if stream:
            payload = (
                b'data: {"id":"m1","choices":[{"delta":{"role":"assistant","content":"hi"},"finish_reason":null}]}\n\n'
                b'data: {"id":"m1","choices":[{"delta":{"content":"!"},"finish_reason":null}],"usage":{"prompt_tokens":5,"completion_tokens":2,"total_tokens":7}}\n\n'
                b"data: [DONE]\n\n"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            data = {
                "id": "m1",
                "model": model,
                "choices": [{"message": {"role": "assistant", "content": "mock reply"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
            }
            payload = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def upstream_url():
    MockUpstream.calls = 0
    server = HTTPServer(("127.0.0.1", 0), MockUpstream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/v1"
    server.shutdown()


@pytest.fixture()
def client(upstream_url, tmp_path):
    cfg = Config(
        api_keys={"default": "test-key"},
        upstream=UpstreamConfig(
            base_url=upstream_url, api_key_env="MOCK_KEY", default_model="deepseek-chat"
        ),
        anchor_prompt="ANCHOR PROTOCOL",
        audit_dir=str(tmp_path / "logs"),
        data_dir=str(tmp_path / "data"),
    )
    return TestClient(create_app(cfg))


def test_health(client):
    assert client.get("/v1/health").json()["status"] == "ok"


def test_auth_fail(client):
    r = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 401


def test_non_stream(client):
    r = client.post(
        "/v1/chat/completions",
        headers={"X-API-Key": "test-key", "X-Project-Id": "p1", "X-Session-Id": "s1"},
        json={"messages": [{"role": "user", "content": "帮我查 k8s 日志"}], "stream": False},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["choices"][0]["message"]["content"] == "mock reply"
    assert data["model"] == "deepseek-chat"


def test_stream(client):
    r = client.post(
        "/v1/chat/completions",
        headers={"X-API-Key": "test-key"},
        json={"messages": [{"role": "user", "content": "hi"}], "stream": True},
    )
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    assert "data: [DONE]" in r.text


def test_models(client):
    r = client.get("/v1/models", headers={"X-API-Key": "test-key"})
    assert r.status_code == 200
    assert r.json()["data"][0]["id"] == "deepseek-chat"


def test_stats_recorded(client):
    client.post(
        "/v1/chat/completions",
        headers={"X-API-Key": "test-key"},
        json={"messages": [{"role": "user", "content": "hi"}], "stream": False},
    )
    snap = client.get("/v1/stats/hits").json()
    assert snap["prefix"]["buckets"]
    assert "exact_cache" in snap


def test_exact_cache_hit_skips_upstream(client):
    body = {"messages": [{"role": "user", "content": "完全相同的请求"}], "stream": False}
    r1 = client.post(
        "/v1/chat/completions", headers={"X-API-Key": "test-key"}, json=body
    )
    assert r1.headers.get("X-Gateway-Cache") == "MISS"
    calls_after_first = MockUpstream.calls
    r2 = client.post(
        "/v1/chat/completions", headers={"X-API-Key": "test-key"}, json=body
    )
    assert r2.status_code == 200
    assert r2.headers.get("X-Gateway-Cache") == "HIT"
    assert MockUpstream.calls == calls_after_first


def test_stream_fills_cache_then_nonstream_hits(client):
    body = {"messages": [{"role": "user", "content": "流式后命中"}], "stream": True}
    r1 = client.post("/v1/chat/completions", headers={"X-API-Key": "test-key"}, json=body)
    assert r1.status_code == 200
    body_ns = dict(body)
    body_ns["stream"] = False
    calls_after = MockUpstream.calls
    r2 = client.post(
        "/v1/chat/completions", headers={"X-API-Key": "test-key"}, json=body_ns
    )
    assert r2.status_code == 200
    assert r2.headers.get("X-Gateway-Cache") == "HIT"
    assert MockUpstream.calls == calls_after


def test_project_registry(client, tmp_path):
    agents = tmp_path / "myproj" / "AGENTS.md"
    agents.parent.mkdir(parents=True)
    agents.write_text("项目规范: 禁止删除Pod", encoding="utf-8")
    r = client.post(
        "/v1/projects",
        headers={"X-API-Key": "test-key"},
        json={"project_id": "myproj", "agents_path": str(agents.parent)},
    )
    assert r.status_code == 200
    assert r.json()["agents_path"] == str(agents.parent)

    listed = client.get("/v1/projects").json()
    assert "myproj" in listed["projects"]

    r = client.delete("/v1/projects/myproj")
    assert r.status_code == 200
