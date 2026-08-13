import json, os
from collections import Counter, defaultdict

p = r"C:\Users\30849\ai-agent-gateway\logs\routing\routing-2026-08-13.jsonl"
rows = [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
rows = [r for r in rows if len(r.get("ts", "")) >= 16 and r["ts"][11:16] >= "10:02"]
print("当前进程路由记录:", len(rows))

# 分类器性能 (只统计实际调用的)
calls = defaultdict(list)
for r in rows:
    for cc in r.get("classifier_calls") or []:
        nm = cc.get("name")
        if nm:
            calls[nm].append((cc.get("ok"), cc.get("latency_ms") or 0))
print("\n=== 当前分类器性能 ===")
for nm, arr in sorted(calls.items(), key=lambda x: -len(x[1])):
    ok = sum(1 for a in arr if a[0])
    lat = [a[1] for a in arr if a[1] > 0]
    avg = sum(lat) / len(lat) if lat else 0
    print(f"  {nm}: 调用{len(arr)} 成功{ok} 失败{len(arr)-ok} 成功率{ok/len(arr)*100:.0f}% 平均延迟{avg:.0f}ms")

# llm 路径的延迟
llm = [r for r in rows if r.get("source") == "llm"]
jl = [r.get("judge_latency_ms", 0) for r in llm if r.get("judge_latency_ms")]
if jl:
    jl.sort()
    print(f"\n=== LLM 分类延迟 ({len(jl)} 次) ===")
    print(f"  avg={sum(jl)/len(jl):.0f} p50={jl[len(jl)//2]} p90={jl[int(len(jl)*0.9)]} max={max(jl)}")

# 轮询位置
pos = Counter()
for r in llm:
    cc = r.get("classifier_calls") or []
    for i, x in enumerate(cc):
        if x.get("ok"):
            pos[f"第{i+1}个成功"] += 1
            break
    else:
        pos["全失败"] = pos.get("全失败", 0) + 1 if cc else 0
print("\n=== LLM 路径轮询位置 ===")
for k, v in sorted(pos.items()):
    print(f"  {k}: {v}")

# route 分布
print("\n=== route 分布 ===")
for k, v in Counter(r.get("route") for r in rows).most_common():
    print(f"  {k}: {v}")

# 是否有多余的权重: config 4 个分类器实际被调用的次数分布
print("\n=== classifier_calls 长度分布 (一次请求调几个) ===")
print(dict(Counter(len(r.get("classifier_calls") or []) for r in llm)))
