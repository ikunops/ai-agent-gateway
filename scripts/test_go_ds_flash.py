"""go-ds-flash (deepseek-v4-flash @go) 作为 LLM 仲裁分类器实测。
模拟网关 ClassifierClient 调用 opencode zen/go 端点, 统计成功率/延迟/消耗。"""
import json
import os
import time
import urllib.request
import urllib.error

BASE = "https://opencode.ai/zen/go/v1"
MODEL = "deepseek-v4-flash"
API_KEY = os.environ.get("OPENCODE_GO_API_KEY", "")
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
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + API_KEY,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        },
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            ms = int((time.time() - t0) * 1000)
            content = ((data.get("choices") or [{}])[0].get("message", {}).get("content", "") or "").strip()
            usage = data.get("usage") or {}
            return ms, content, resp.status, usage
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        return int((time.time() - t0) * 1000), "", e.code, {"err_body": body}
    except Exception as e:
        return int((time.time() - t0) * 1000), "", str(e)[:60], {}


def parse_route(content):
    cl = content.lower()
    for r in ROUTES:
        if r in cl:
            return r
    return None


print(f"=== go-ds-flash 实测 (model={MODEL}) ===")
print(f"端点: {BASE}/chat/completions | key: {'有(' + API_KEY[:10] + '...)' if API_KEY else '无!'}")
print()
ok = fail = 0
lats = []
tok_in = tok_out = 0
for expect, text in TESTS:
    ms, content, status, usage = call(text)
    got = parse_route(content)
    good = (status == 200 and got is not None)
    if good:
        ok += 1
        lats.append(ms)
        tok_in += usage.get("prompt_tokens", 0) or 0
        tok_out += usage.get("completion_tokens", 0) or 0
    else:
        fail += 1
    flag = "OK " if good else "FAIL"
    print(f"[{flag}] 期望={expect:12s} 得到={str(got):12s} {ms:5d}ms status={status} content={content[:40]!r}")
    time.sleep(0.3)

print()
print(f"成功率: {ok}/{len(TESTS)} = {ok/len(TESTS)*100:.0f}%")
if lats:
    lats.sort()
    print(f"延迟(成功): avg={sum(lats)//len(lats)}ms p50={lats[len(lats)//2]}ms p90={lats[int(len(lats)*0.9)]}ms max={max(lats)}ms")
print(f"Token 消耗: 输入 {tok_in} + 输出 {tok_out} = {tok_in+tok_out} tokens")
print(f"估算费用: 输入 ${tok_in/1e6*0.14:.4f} + 输出 ${tok_out/1e6*0.28:.4f} = ${(tok_in/1e6*0.14 + tok_out/1e6*0.28):.4f}")
