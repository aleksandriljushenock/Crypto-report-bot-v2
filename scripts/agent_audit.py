#!/usr/bin/env python3
"""Repository audit used by humans and coding agents. Fails closed in --strict mode."""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRET_NAME = re.compile(r"^\.env(?!.*\.example$)", re.I)
SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\b"),
]
RUNTIME_DIRS = {"logs", "data", "models", "checkpoints", "backups", ".venv", "venv", "__pycache__", ".pytest_cache", "dist"}
BINARY_SUFFIX = {".db", ".sqlite", ".sqlite3", ".joblib", ".pkl", ".zip", ".pyc"}


def audit():
    findings=[]
    required=["AGENTS.md","agent/policy.yml",".github/workflows/ci.yml",".github/workflows/agent-maintenance.yml",".github/workflows/agent-auto-merge.yml",".github/workflows/release.yml"]
    for rel in required:
        if not (ROOT/rel).exists(): findings.append({"severity":"error","kind":"missing_required","path":rel})
    version=(ROOT/"VERSION").read_text().strip() if (ROOT/"VERSION").exists() else ""
    if not re.fullmatch(r"\d+\.\d+\.\d+",version): findings.append({"severity":"error","kind":"bad_version","value":version})
    for p in ROOT.rglob("*"):
        if not p.is_file(): continue
        rel=p.relative_to(ROOT)
        if ".git" in rel.parts: continue
        if SECRET_NAME.match(p.name) and not p.name.endswith(".example"):
            findings.append({"severity":"error","kind":"secret_filename","path":str(rel)})
        if p.suffix.lower() in BINARY_SUFFIX and not any(part in RUNTIME_DIRS for part in rel.parts):
            findings.append({"severity":"warning","kind":"binary_artifact","path":str(rel)})
        if any(part in RUNTIME_DIRS for part in rel.parts): continue
        if p.stat().st_size > 2_000_000:
            findings.append({"severity":"warning","kind":"large_source_file","path":str(rel),"bytes":p.stat().st_size})
        if p.suffix.lower() in {".py",".md",".txt",".yml",".yaml",".json",".sh",".example",""}:
            try: text=p.read_text(errors="ignore")
            except Exception: continue
            if not p.name.endswith(".example"):
                for pat in SECRET_PATTERNS:
                    if pat.search(text):
                        findings.append({"severity":"error","kind":"possible_secret","path":str(rel)})
                        break
    return version, findings

if __name__ == "__main__":
    ap=argparse.ArgumentParser(); ap.add_argument("--strict",action="store_true"); ap.add_argument("--json",action="store_true")
    args=ap.parse_args(); version, findings=audit()
    payload={"version":version,"findings":findings,"errors":sum(x["severity"]=="error" for x in findings),"warnings":sum(x["severity"]=="warning" for x in findings)}
    print(json.dumps(payload,ensure_ascii=False,indent=2) if args.json else f"agent-audit version={version} errors={payload['errors']} warnings={payload['warnings']}")
    if not args.json:
        for x in findings: print(f"{x['severity'].upper()}: {x['kind']} {x.get('path',x.get('value',''))}")
    if args.strict and payload["errors"]: sys.exit(2)
