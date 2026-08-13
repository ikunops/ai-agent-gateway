import json
import time
import urllib.request
import urllib.error
import random

BASE = "https://api.kilo.ai/api/openrouter"
MODEL = "kilo-auto/free"
ROUTES = ["java", "oracle", "mysql", "kubernetes", "linux", "network", "generic"]

POOL = [
    ("java", "JVM 参数 -Xms -Xmx 设置多少合适，堆外内存和堆内存怎么平衡？"),
    ("java", "ConcurrentHashMap 在 JDK8 的实现，锁分段还是 CAS？"),
    ("oracle", "AWR 报告里 DB Time 占比高，怎么定位等待事件？"),
    ("mysql", "InnoDB 缓冲池命中率低，怎么调 innodb_buffer_pool_size？"),
    ("kubernetes", "HPA 不生效，metrics-server 指标拿不到，怎么排查？"),
    ("linux", "磁盘 IO 打满，iostat 显示 await 高，是硬件还是队列问题？"),
    ("network", "跨机房延迟高，丢包率多少算异常，怎么用 mtr 定位？"),
    ("generic", "帮我推荐一本讲操作系统的书"),
    ("mysql", "慢查询日志太大，怎么分析 top N 慢 SQL？"),
    ("kubernetes", "Service 负载不均衡，endpoints 数量不对，怎么修？"),
    ("java", "G1 GC 频繁 Full GC，Region 和 RememberSet 怎么调优？"),
    ("linux", "内存充足但 swap 被占用，vm.swappiness 应该设多少？"),
    ("oracle", "数据泵导出慢，并行度参数怎么设置？"),
    ("kubernetes", "Pod 启动慢，镜像拉取超时，怎么加速？"),
    ("network", "tcp_tw_reuse 和 tcp_tw_recycle 的区别？"),
    ("java", "线程池拒绝策略 CallerRunsPolicy 什么时候合适？"),
    ("mysql", "死锁怎么解决，show engine innodb status 怎么看？"),
    ("generic", "周末有什么推荐的电影？"),
]

def call(text):
    routes = ", ".join(ROUTES)
    prompt = (
        f"你有以下路线：{routes}。\n用户问题：\"{text}\"\n"
        "请只输出最匹配的一条路线名，不要输出其他内容。"
    )
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
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            ms = int((time.time() - t0) * 1000)
            content = ((data.get("choices") or [{}])[0].get("message", {}).get("content", "") or "").strip()
            return ms, content, resp.status
    except urllib.error.HTTPError as e:
        return int((time.time() - t0) * 1000), "", e.code
    except Exception as e:
        return int((time.time() - t0) * 1000), "", str(e)[:60]

random.seed(7)
sample = random.sample(POOL, 10)

ok = fail = 0
lats = []
status_counts = {}
for expect, text in sample:
    ms, content, status = call(text)
    got = next((r for r in ROUTES if r in content.lower()), None)
    good = (status == 200 and got is not None)
    if good:
        ok += 1
        lats.append(ms)
    else:
        fail += 1
    status_counts[str(status)] = status_counts.get(str(status), 0) + 1
    flag = "OK " if good else "FAIL"
    print(f"[{flag}] 期望={expect:10s} 得到={str(got):10s} {ms:5d}ms status={status} content={content[:30]!r}")
    time.sleep(0.3)

print()
print(f"成功率: {ok}/{len(sample)} = {ok/len(sample)*100:.0f}%")
print("status 分布:", status_counts)
if lats:
    lats.sort()
    print(f"延迟: avg={sum(lats)//len(lats)}ms p50={lats[len(lats)//2]}ms p90={lats[int(len(lats)*0.9)]}ms max={max(lats)}ms")
