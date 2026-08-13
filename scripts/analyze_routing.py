import json, os
from collections import Counter, defaultdict

logs = [
    r"C:\Users\30849\ai-agent-gateway\logs\routing\routing-2026-08-13.jsonl",
    r"C:\Users\30849\ai-agent-gateway\logs\routing\routing-2026-08-12.jsonl",
]

rows = []
for p in logs:
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass

print("总记录:", len(rows), "| 日期:", sorted(set(r["ts"][:10] for r in rows)))

# 1. 来源分布
print("\n=== 路由来源 (source) ===")
c = Counter(r.get("source") for r in rows)
for k, v in c.most_common():
    print(f"  {k}: {v}")

# 2. tier 分布
print("\n=== tier 分布 ===")
c = Counter(r.get("tier") for r in rows)
for k, v in sorted(c.items()):
    print(f"  tier {k}: {v}")

# 3. route 分布
print("\n=== 路由目标 ===")
c = Counter(r.get("route") for r in rows)
for k, v in c.most_common():
    print(f"  {k}: {v}")

# 4. 分类器调用统计: 每个分类器 ok/fail 次数 + 平均延迟
print("\n=== 分类器调用 ===")
calls = defaultdict(list)  # name -> [ok, latency]
for r in rows:
    for cc in r.get("classifier_calls") or []:
        nm = cc.get("name")
        if nm is None:
            continue
        calls[nm].append((cc.get("ok"), cc.get("latency_ms") or 0))
for nm, arr in sorted(calls.items(), key=lambda x: -len(x[1])):
    ok = sum(1 for a in arr if a[0])
    lat = [a[1] for a in arr if a[1] > 0]
    avg = sum(lat) / len(lat) if lat else 0
    print(f"  {nm}: 调用{len(arr)} ok={ok} fail={len(arr)-ok} 成功率={ok/len(arr)*100:.0f}% 平均延迟={avg:.0f}ms")

# 5. classifier_stage 分布
print("\n=== 分类器阶段 ===")
c = Counter(r.get("classifier_stage") for r in rows)
for k, v in c.most_common():
    print(f"  {k}: {v}")

# 6. 延迟统计
print("\n=== 延迟 ===")
jl = [r.get("judge_latency_ms", 0) for r in rows if r.get("judge_latency_ms")]
vl = [r.get("vector_latency_ms", 0) for r in rows if r.get("vector_latency_ms")]
for name, arr in (("judge(总分类延迟)", jl), ("vector", vl)):
    if arr:
        arr.sort()
        print(f"  {name}: avg={sum(arr)/len(arr):.0f} p50={arr[len(arr)//2]} p90={arr[int(len(arr)*0.9)]} max={max(arr)} n={len(arr)}")

# 7. 分类器首次命中率: 第一个 ok 的调用在什么位置
print("\n=== 分类器轮询: 需要几次调用才成功 ===")
pos = Counter()
for r in rows:
    cc = r.get("classifier_calls") or []
    for i, x in enumerate(cc):
        if x.get("ok"):
            pos[i + 1] += 1
            break
    else:
        if cc:
            pos["全失败"] = pos.get("全失败", 0) + 1
for k, v in sorted(pos.items(), key=lambda x: (isinstance(x[0], int), x[0])):
    print(f"  第{k}个调用成功: {v}")

# 8. 无 classifier_calls 的 (纯向量/规则)
novotes = [r for r in rows if not (r.get("classifier_calls") or [])]
print(f"\n=== 无分类器调用(纯向量/规则): {len(novotes)} ===")
c = Counter(r.get("source") for r in novotes)
for k, v in c.most_common():
    print(f"  {k}: {v}")

# 9. 权限拦截
print("\n=== 权限 ===")
c = Counter(r.get("perm_action") for r in rows)
print(dict(c))
