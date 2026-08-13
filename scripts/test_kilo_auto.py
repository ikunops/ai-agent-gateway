"""kilo-auto/free 分类器实测: 模拟网关分类器调用 api.kilo.ai/api/openrouter。
判断当前实际成功率/延迟/返回质量, 决定是否值得留在轮询池。"""
import json
import time
import urllib.request
import urllib.error

BASE = "https://api.kilo.ai/api/openrouter"
MODEL = "kilo-auto/free"
ROUTES = ["java", "oracle", "mysql", "kubernetes", "linux", "network", "generic"]

PROMPT_TPL = (
    "你有以下路线：{routes}。\n"
    "用户问题：\"{text}\"\n"
    "规则：\n"
    "1. 如果提供了对话上下文，必须优先依据上下文判断技术语境，把上下文中最突出的技术领域作为路线。\n"
    "2. 仅在没有任何上下文时，理论性问题（什么是/解释/原理）才选 generic。\n"
    "3. 否则根据问题涉及的技术领域选择最匹配的一条路线。\n"
    "请只输出最匹配的一条路线名，不要输出其他内容。"
)

TESTS = [
    ("java", "JVM 堆外内存溢出 OOM 怎么排查，用 jmap 看还是 arthas？"),
    ("mysql", "MySQL 主从复制延迟大，binlog 刷盘策略怎么调？"),
    ("kubernetes", "Pod 一直 CrashLoopBackOff，怎么定位是镜像问题还是资源问题？"),
    ("linux", "top 显示 load average 高但 CPU 不高，是不是 IO 瓶颈？"),
    ("network", "防火墙放行了 443 但还是连不上，tcpdump 看 SYN 重传怎么分析？"),
    ("generic", "今天天气不错，有什么推荐的咖啡？"),
    ("oracle", "Oracle RAC 两个节点负载不均，ASM 磁盘组怎么均衡？"),
    ("java", "Spring 事务传播机制 REQUIRES_NEW 和 NESTED 的区别？"),
    ("kubernetes", "K8s 里 PVC 一直 Pending，StorageClass 不匹配怎么排查？"),
    ("mysql", "索引失效了，explain 显示 type=ALL，该怎么优化这个慢查询？"),
]


def call(text):
    routes = ", ".join(ROUTES)
    prompt = PROMPT_TPL.format(routes=routes, text=text)
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "你是一个路由分类器，只输出路线名。"},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 256,
        "temperature": 0,
        "stream": False,
    }
    req = urllib.request.Request(
        BASE + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            ms = int((time.time() - t0) * 1000)
            content = ((data.get("choices") or [{}])[0].get("message", {}).get("content", "") or "").strip()
            return ms, content, resp.status
    except urllib.error.HTTPError as e:
        ms = int((time.time() - t0) * 1000)
        return ms, "", e.code
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        return ms, "", str(e)[:80]


def parse_route(content):
    cl = content.lower()
    for r in ROUTES:
        if r in cl:
            return r
    return None


print(f"=== kilo-auto/free 实测 ({MODEL}) ===")
print(f"端点: {BASE}/chat/completions")
print()
ok, fail = 0, 0
lats = []
for expect, text in TESTS:
    ms, content, status = call(text)
    got = parse_route(content)
    match = (got == expect) if got else False
    good = (status == 200 and got is not None)
    if good:
        ok += 1
        lats.append(ms)
    else:
        fail += 1
    flag = "OK " if good else "FAIL"
    print(f"[{flag}] 期望={expect:12s} 得到={str(got):12s} {ms:5d}ms status={status} content={content[:40]!r}")
    time.sleep(0.5)

print()
print(f"成功率: {ok}/{len(TESTS)} = {ok/len(TESTS)*100:.0f}%")
if lats:
    lats.sort()
    print(f"延迟(成功): avg={sum(lats)//len(lats)}ms p50={lats[len(lats)//2]}ms p90={lats[int(len(lats)*0.9)]}ms max={max(lats)}ms")
