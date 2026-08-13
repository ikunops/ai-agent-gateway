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


class _Fail500(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        self.send_response(500)
        self.send_header("Content-Length", "0")
        self.end_headers()

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
        assert meta["votes"] == {"oracle": 1.0}
        assert len(meta["per_classifier"]) == 1
    finally:
        s1.shutdown(), s2.shutdown()


def test_ensemble_picks_one_via_round_robin():
    s1, s2 = _serve("oracle"), _serve("java")
    try:
        ens = ClassifierEnsemble([_client(f"http://127.0.0.1:{s1.server_port}/v1", "a"),
                                  _client(f"http://127.0.0.1:{s2.server_port}/v1", "b")])

        async def _loop():
            return {await ens.judge("存储心跳超时", list(PROFILES)) for _ in range(4)}

        names = run(_loop())
        assert names == {"oracle", "java"}
        assert ens.total_weight == 2.0
    finally:
        s1.shutdown(), s2.shutdown()


def test_ensemble_round_robin_falls_back_to_next_on_failure():
    class _Fail(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    fail = HTTPServer(("127.0.0.1", 0), _Fail)
    threading.Thread(target=fail.serve_forever, daemon=True).start()
    ok = _serve("oracle")
    try:
        ens = ClassifierEnsemble([
            ClassifierClient("a", f"http://127.0.0.1:{fail.server_port}/v1", "", "local-model", weight=1.0),
            ClassifierClient("b", f"http://127.0.0.1:{ok.server_port}/v1", "", "local-model", weight=1.0),
        ])
        name, meta = run(ens.judge_detailed("x", list(PROFILES)))
        assert name == "oracle"
        assert len(meta["per_classifier"]) == 2
        assert meta["per_classifier"][0]["ok"] is False
        assert meta["per_classifier"][1]["ok"] is True
    finally:
        fail.shutdown(), ok.shutdown()


def test_ensemble_weighted_vote():
    s1, s2 = _serve("oracle"), _serve("java")
    try:
        ens = ClassifierEnsemble([_client(f"http://127.0.0.1:{s1.server_port}/v1", "a", weight=2.0),
                                  _client(f"http://127.0.0.1:{s2.server_port}/v1", "b", weight=1.0)])
        name, meta = run(ens.judge_detailed("存储心跳超时", list(PROFILES)))
        assert name == "oracle"
        assert meta["votes"] == {"oracle": 2.0}
    finally:
        s1.shutdown(), s2.shutdown()


def test_ensemble_all_fail_returns_none():
    server = HTTPServer(("127.0.0.1", 0), _Fail500)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        ens = ClassifierEnsemble([_client(f"http://127.0.0.1:{server.server_port}/v1", "x")])
        result = run(ens.judge("存储心跳超时", list(PROFILES)))
        assert result is None
    finally:
        server.shutdown()


def test_ensemble_local_all_fail_falls_back_to_remote():
    """free(local) 全失败后降级到 go(remote) 兜底 —— 两级调度核心场景。"""
    fail = HTTPServer(("127.0.0.1", 0), _Fail500)
    threading.Thread(target=fail.serve_forever, daemon=True).start()
    ok = _serve("oracle")
    try:
        ens = ClassifierEnsemble([
            ClassifierClient("free-a", f"http://127.0.0.1:{fail.server_port}/v1", "", "m", weight=1.0, local=True),
            ClassifierClient("free-b", f"http://127.0.0.1:{fail.server_port}/v1", "", "m", weight=1.0, local=True),
            ClassifierClient("go-c", f"http://127.0.0.1:{ok.server_port}/v1", "", "m", weight=1.0, local=False),
        ])
        name, meta = run(ens.judge_detailed("x", list(PROFILES)))
        assert name == "oracle"
        assert meta["stage"] == "remote"
        assert len(meta["per_classifier"]) == 3
        assert all(p["ok"] is False for p in meta["per_classifier"][:2])
        assert meta["per_classifier"][2]["ok"] is True
    finally:
        fail.shutdown(), ok.shutdown()


def test_ensemble_local_ok_never_touches_remote():
    """local(free) 成功时完全不碰 remote(go)，省付费。"""
    ok_local = _serve("oracle")
    ok_remote = _serve("java")
    try:
        ens = ClassifierEnsemble([
            ClassifierClient("free-a", f"http://127.0.0.1:{ok_local.server_port}/v1", "", "m", weight=1.0, local=True),
            ClassifierClient("go-b", f"http://127.0.0.1:{ok_remote.server_port}/v1", "", "m", weight=1.0, local=False),
        ])
        name, meta = run(ens.judge_detailed("x", list(PROFILES)))
        assert name == "oracle"
        assert meta["stage"] == "local:free-a"
        assert len(meta["per_classifier"]) == 1
    finally:
        ok_local.shutdown(), ok_remote.shutdown()


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
        assert meta["vote"]["votes"] == {"oracle": 1.0}
    finally:
        s1.shutdown(), s2.shutdown()
