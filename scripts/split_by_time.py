import json, os
from collections import Counter, defaultdict

p = r"C:\Users\30849\ai-agent-gateway\logs\routing\routing-2026-08-13.jsonl"
rows = [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]

# 按时间切分: 10:02 前后 (进程重启点)
before, after = [], []
for r in rows:
    ts = r.get("ts", "")
    hhmm = ts[11:16] if len(ts) >= 16 else "00:00"
    (before if hhmm < "10:02" else after).append(r)

print(f"总 {len(rows)} | 10:02前(旧进程) {len(before)} | 10:02后(当前进程) {len(after)}")

for label, grp in (("旧进程(<10:02)", before), ("当前进程(>=10:02)", after)):
    if not grp:
        continue
    print(f"\n=== {label} ===")
    c = Counter()
    for r in grp:
        stage = r.get("classifier_stage") or "none"
        c[stage] += 1
    print("stage 分布:", dict(c))
    names = Counter()
    for r in grp:
        for cc in r.get("classifier_calls") or []:
            names[cc.get("name")] += 1
    print("调用分类器:", dict(names))
    sr = Counter(r.get("source") for r in grp)
    print("source:", dict(sr))
