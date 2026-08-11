import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.layers.router import SemanticRouter
from app.upstream.classifier import ClassifierClient, ClassifierEnsemble

PROFILES = {
    "java": "Java/JVM生态：JVM内存模型、OOM、并发编程、Spring生态",
    "oracle": "Oracle数据库：RAC高可用、ASM存储、SQL调优、AWR",
    "generic": "通用问题、编程语言语法、日常IT咨询",
}


class _ReplyMock(BaseHTTPRequestHandler):
    route_name = "oracle"

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        data = {"choices": [{"message": {"content": type(self).route_name}}]}
        payload = json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


def _serve(route_name):
    """每个 server 独立闭包持有自己的路线名（避免共享类属性串台）。"""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            data = {"choices": [{"message": {"content": route_name}}]}
            payload = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def run(coro):
    return asyncio.run(coro)


def _client(url, name, weight=1.0):
    return ClassifierClient(name, url, "", "local-model", weight=weight)


def test_ensemble_agrees():
    s1, s2 = _serve("oracle"), _serve("oracle")
    try:
        ens = ClassifierEnsemble([_client(f"http://127.0.0.1:{s1.server_port}/v1", "a"),
                                  _client(f"http://127.0.0.1:{s2.server_port}/v1", "b")])
        name, meta = run(ens.judge_detailed("存储心跳超时", list(PROFILES)))
        assert name == "oracle"
        assert meta["agreement"] == 1.0
        assert meta["votes"] == {"oracle": 2.0}
    finally:
        s1.shutdown(), s2.shutdown()


def test_ensemble_disagreement_picks_top_vote():
    s1, s2, s3 = _serve("oracle"), _serve("oracle"), _serve("java")
    try:
        ens = ClassifierEnsemble([_client(f"http://127.0.0.1:{s1.server_port}/v1", "a"),
                                  _client(f"http://127.0.0.1:{s2.server_port}/v1", "b"),
                                  _client(f"http://127.0.0.1:{s3.server_port}/v1", "c")])
        name, meta = run(ens.judge_detailed("存储心跳超时", list(PROFILES)))
        assert name == "oracle"
        assert meta["agreement"] == round(2 / 3, 3)
        assert len(meta["per_classifier"]) == 3
    finally:
        s1.shutdown(), s2.shutdown(), s3.shutdown()


def test_ensemble_weighted_vote():
    s1, s2 = _serve("oracle"), _serve("java")
    try:
        ens = ClassifierEnsemble([_client(f"http://127.0.0.1:{s1.server_port}/v1", "a", weight=2.0),
                                  _client(f"http://127.0.0.1:{s2.server_port}/v1", "b", weight=1.0)])
        name, meta = run(ens.judge_detailed("存储心跳超时", list(PROFILES)))
        assert name == "oracle"
        assert meta["agreement"] == round(2 / 3, 3)
    finally:
        s1.shutdown(), s2.shutdown()


def test_ensemble_all_fail_returns_none():
    class _Fail(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(500)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), _Fail)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        ens = ClassifierEnsemble([_client(f"http://127.0.0.1:{server.server_port}/v1", "x")])
        result = run(ens.judge("存储心跳超时", list(PROFILES)))
        assert result is None
    finally:
        server.shutdown()


def test_route_detailed_with_ensemble_vote_meta():
    s1, s2 = _serve("oracle"), _serve("oracle")
    try:
        router = SemanticRouter(PROFILES, threshold=0.99)
        ens = ClassifierEnsemble([_client(f"http://127.0.0.1:{s1.server_port}/v1", "a"),
                                  _client(f"http://127.0.0.1:{s2.server_port}/v1", "b")])
        name, score, source, meta = run(
            router.route_detailed("RAC ASM 存储心跳超时", llm_judge=ens.judge_detailed)
        )
        assert source == "llm"
        assert name == "oracle"
        assert score == 1.0
        assert meta["vote"]["agreement"] == 1.0
        assert meta["vote"]["votes"] == {"oracle": 2.0}
    finally:
        s1.shutdown(), s2.shutdown()
