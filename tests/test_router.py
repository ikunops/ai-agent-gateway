import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.layers.router import SemanticRouter
from app.upstream.llm_router import LLMRouter

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
    assert source in ("vector", "cache")
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


def test_llm_parse():
    assert LLMRouter._parse("oracle", ["java", "oracle", "linux"]) == "oracle"
    assert LLMRouter._parse("Oracle", ["java", "oracle"]) == "oracle"
    assert LLMRouter._parse("kubernetes", ["java", "oracle", "kubernetes"]) == "kubernetes"
    assert LLMRouter._parse("我不知道", ["java", "oracle"]) is None
