import csv
from collections import defaultdict

path = "benchmarks/results/resource_usage.csv"
cpu = defaultdict(list)
mem_last = {}
with open(path, newline="", encoding="utf-8") as f:
    r = csv.DictReader(f)
    for row in r:
        c = row["container"]
        try:
            cpu[c].append(float(row["cpu_percent"].strip("%")))
        except ValueError:
            pass
        mem_last[c] = row["mem_usage"]

for c in sorted(cpu):
    vals = cpu[c]
    print(f"{c}: cpu avg={sum(vals)/len(vals):.2f}% max={max(vals):.2f}% n={len(vals)} mem={mem_last[c]}")
