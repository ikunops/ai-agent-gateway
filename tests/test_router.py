import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.layers.router import SemanticRouter
from app.upstream.classifier import ClassifierClient

PROFILES = {
    "java": "Java/JVM生态：JVM内存模型、OOM、堆外内存、并发编程、Maven/Gradle构建、Spring生态、性能调优",
    "oracle": "Oracle数据库：关系型数据库集群、RAC高可用、ASM存储管理、SQL调优、备份恢复、数据泵、分区、AWR",
    "mysql": "MySQL数据库：InnoDB、主从复制、binlog、慢查询、索引优化、锁、GTID、半同步复制",
    "kubernetes": "Kubernetes/K8s：容器编排、Pod调度、CrashLoopBackOff、服务网格、云原生基础设施、Helm、节点资源管理",
    "linux": "Linux操作系统：进程管理、文件系统、网络配置、Shell脚本、系统服务、内核参数",
    "network": "网络：网络协议、路由交换、负载均衡、防火墙、DNS、网络故障排查、连通性",
    "generic": "通用问题、编程语言语法、日常IT咨询、与具体技术栈无关的问题",
}


def run(coro):
    return asyncio.run(coro)


def test_route_cache_hit_skips_judge():
    router = SemanticRouter(PROFILES, threshold=0.0)
    calls = []

    async def judge(text, names, context=""):
        calls.append(text)
        return "oracle"

    n1, s1, src1 = run(router.route("RAC ASM 存储心跳超时", llm_judge=judge))
    assert src1 in ("vector", "llm")
    n2, s2, src2 = run(router.route("RAC ASM 存储心跳超时"))
    assert src2 == "cache"
    assert n1 == n2
    assert len(calls) <= 1


def test_llm_judge_when_vector_below_threshold():
    router = SemanticRouter(PROFILES, threshold=0.99)

    async def judge(text, names, context=""):
        return "oracle"

    name, score, source = run(router.route("RAC ASM 存储心跳超时", llm_judge=judge))
    assert source == "llm"
    assert name == "oracle"


def test_vector_route_clear_term():
    router = SemanticRouter(PROFILES, threshold=0.3)
    name, score, source = run(router.route("JVM 内存溢出，堆外内存不断增长"))
    assert source in ("vector", "cache")
    assert name == "java"


def test_vector_route_mysql():
    router = SemanticRouter(PROFILES, threshold=0.3)
    name, score, source = run(router.route("MySQL 主从延迟，binlog 堆积严重"))
    assert source in ("term", "vector", "cache")
    assert name == "mysql"


def test_context_passed_to_judge():
    router = SemanticRouter(PROFILES, threshold=0.99)
    seen = {}

    async def judge(text, names, context=""):
        seen["context"] = context
        return "oracle"

    run(router.route("什么是分布式事务", llm_judge=judge, context="user: MySQL 主从延迟怎么办"))
    assert "MySQL" in seen.get("context", "")


def test_fallback_when_all_fail():
    router = SemanticRouter(PROFILES, threshold=0.99)
    name, score, source = run(router.route("今天天气怎么样"))
    assert source == "fallback"


def test_term_fast_path_later_paragraph():
    router = SemanticRouter(PROFILES, threshold=0.99)
    text = "第一段闲聊，今天天气不错。\n\n第二段也是闲聊。\n\n第三段 MySQL 主从延迟，binlog 堆积严重"
    name, score, source = run(router.route(text))
    assert source == "term"
    assert name == "mysql"


def test_term_fast_path_alias():
    router = SemanticRouter(PROFILES, threshold=0.99)
    name, score, source = run(router.route("k8s 节点 CrashLoopBackOff"))
    assert source == "term"
    assert name == "kubernetes"


def test_vector_per_segment_max_with_filler():
    router = SemanticRouter(PROFILES, threshold=0.3)
    text = "我们先聊聊天气和午饭\n\n再说说 JVM 内存溢出 堆外内存不断增长"
    name, score, source = run(router.route(text))
    assert source in ("vector", "cache")
    assert name == "java"


def test_judge_receives_digest_not_raw_text():
    router = SemanticRouter(PROFILES, threshold=0.99)
    seen = {}

    async def judge(text, names, context=""):
        seen["text"] = text
        return "kubernetes"

    text = "第一段闲聊。\n\n第二段闲聊。\n\n第三段 Pod 频繁重启 CrashLoopBackOff\n\n第四段收尾"
    name, _, source = run(router.route(text, llm_judge=judge))
    assert source == "llm"
    assert name == "kubernetes"
    assert "第三段 Pod 频繁重启" in seen["text"]
    assert "第四段收尾" in seen["text"]


class MockPicker:
    def __init__(self, result):
        self.result = result

    async def pick(self, text):
        return self.result


def test_route_uses_picker_digest():
    router = SemanticRouter(
        PROFILES,
        threshold=0.99,
        picker=MockPicker(["这段讲 Pod 频繁重启 CrashLoopBackOff"]),
    )
    seen = {}

    async def judge(text, names, context=""):
        seen["text"] = text
        return "kubernetes"

    base = "第一段闲聊。\n\n第二段闲聊。\n\n这段讲 Pod 频繁重启 CrashLoopBackOff\n\n第四段收尾。\n\n"
    name, _, source = run(router.route(base * 6, llm_judge=judge))
    assert source == "llm"
    assert name == "kubernetes"
    assert "这段讲 Pod 频繁重启" in seen["text"]


def test_route_falls_back_when_picker_fails():
    router = SemanticRouter(PROFILES, threshold=0.99, picker=MockPicker(None))
    seen = {}

    async def judge(text, names, context=""):
        seen["text"] = text
        return "kubernetes"

    base = "第一段闲聊。\n\n第二段闲聊。\n\n这段讲 Pod 频繁重启 CrashLoopBackOff\n\n第四段收尾。\n\n"
    name, _, source = run(router.route(base * 6, llm_judge=judge))
    assert source == "llm"
    assert name == "kubernetes"
    assert "第一段闲聊" in seen["text"]


def test_route_detailed_meta_term():
    router = SemanticRouter(PROFILES, threshold=0.3)
    name, score, source, meta = run(
        router.route_detailed("第三段 MySQL 主从延迟 binlog 堆积")
    )
    assert source == "term"
    assert name == "mysql"
    assert "mysql" in meta["terms"]
    assert meta["segments"] >= 1


def test_route_detailed_meta_llm_digest():
    router = SemanticRouter(PROFILES, threshold=0.99)

    async def judge(text, names, context=""):
        return "kubernetes"

    base = "第一段闲聊。\n\n第二段闲聊。\n\n这段讲 Pod 频繁重启 CrashLoopBackOff\n\n第四段收尾。\n\n"
    name, score, source, meta = run(
        router.route_detailed(base * 6, llm_judge=judge)
    )
    assert source == "llm"
    assert name == "kubernetes"
    assert meta["digest_len"] > 0
    assert "judge_latency_ms" in meta
    assert "vector_latency_ms" in meta
    assert "best_segment" in meta


def test_route_detailed_meta_vector_scores():
    router = SemanticRouter(PROFILES, threshold=0.3)
    name, score, source, meta = run(
        router.route_detailed("MySQL 主从延迟，binlog 堆积严重")
    )
    assert source in ("term", "vector", "cache")
    if source == "vector":
        assert "vector_scores" in meta


def test_llm_parse():
    assert ClassifierClient._parse("oracle", ["java", "oracle", "linux"]) == "oracle"
    assert ClassifierClient._parse("Oracle", ["java", "oracle"]) == "oracle"
    assert ClassifierClient._parse("kubernetes", ["java", "oracle", "kubernetes"]) == "kubernetes"
    assert ClassifierClient._parse("我不知道", ["java", "oracle"]) is None


def test_llm_judge_without_api_key():
    """无 key 也能调分类器（本地 Ollama/中转站免密钥），失败自然降级。"""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Mock(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            auth = self.headers.get("Authorization", "")
            data = {
                "choices": [{"message": {"content": "oracle" if not auth else "linux"}}]
            }
            payload = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Mock)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        r = ClassifierClient("local", f"http://127.0.0.1:{server.server_port}/v1", "", "local-model")
        name = run(r.judge("存储心跳超时", ["java", "oracle", "linux"]))
        assert name == "oracle"
    finally:
        server.shutdown()
