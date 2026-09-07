#!/usr/bin/env python3
"""Fail-closed production gate for execution diagnostic JSON."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

def walk(obj):
    if isinstance(obj,dict):
        yield obj
        for v in obj.values(): yield from walk(v)
    elif isinstance(obj,list):
        for v in obj: yield from walk(v)

def pick_best(doc):
    candidates=[]
    for d in walk(doc):
        if not isinstance(d,dict): continue
        if any(k in d for k in ("walk_forward_aggregate_pf","walk_forward_aggregate_expectancy","walk_forward_total_trades")):
            candidates.append(d)
    return max(candidates,key=lambda d: float(d.get("walk_forward_total_trades") or 0),default={})

ap=argparse.ArgumentParser(); ap.add_argument("diagnostic"); ap.add_argument("--min-pf",type=float,default=1.15); ap.add_argument("--min-trades",type=int,default=60); ap.add_argument("--max-dd",type=float,default=25.0)
args=ap.parse_args(); doc=json.loads(Path(args.diagnostic).read_text()); d=pick_best(doc)
metrics={
 "pf":d.get("walk_forward_aggregate_pf"),
 "expectancy":d.get("walk_forward_aggregate_expectancy"),
 "ci_low":d.get("walk_forward_expectancy_ci_low"),
 "trades":d.get("walk_forward_total_trades"),
 "dd":d.get("walk_forward_max_drawdown",d.get("walk_forward_aggregate_max_drawdown")),
}
print(json.dumps(metrics,indent=2))
fail=[]
try:
    if float(metrics["pf"]) < args.min_pf: fail.append("pf")
    if float(metrics["expectancy"]) <= 0: fail.append("expectancy")
    if float(metrics["ci_low"]) <= 0: fail.append("ci_low")
    if int(metrics["trades"]) < args.min_trades: fail.append("trades")
    if metrics["dd"] is not None and float(metrics["dd"]) > args.max_dd: fail.append("drawdown")
except (TypeError,ValueError): fail.append("missing_metrics")
if fail:
    print("PROFITABILITY_GATE_FAIL="+",".join(fail)); sys.exit(3)
print("PROFITABILITY_GATE_PASS")
