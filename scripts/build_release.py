#!/usr/bin/env python3
"""Fail-closed release builder: tracked/source allowlist + secret scan."""
from pathlib import Path
import re, zipfile, sys
ROOT=Path(__file__).resolve().parents[1]
OUT=Path(sys.argv[1] if len(sys.argv)>1 else ROOT/'dist'/'Crypto-report-bot-v58.6.zip')
EXCLUDE={'.git','.pytest_cache','__pycache__','logs','data','models','checkpoints','backups','.venv','venv','env','dist'}
SECRET_NAMES=re.compile(r'^\.env(?!.*\.example$)',re.I)
SECRET_PATTERNS=[re.compile(r'\bsk-[A-Za-z0-9_-]{20,}\b'), re.compile(r'(?i)service_role_[A-Za-z0-9._-]{24,}')]
ALLOW_SUFFIX={'.py','.md','.txt','.json','.yml','.yaml','.toml','.ini','.cfg','.sql','.sh','.example'}
files=[]; findings=[]
for p in ROOT.rglob('*'):
    if not p.is_file(): continue
    rel=p.relative_to(ROOT)
    if any(part in EXCLUDE for part in rel.parts): continue
    if p.suffix=='.zip' or p.name.endswith(('.pyc','.pyo','.joblib','.db','.sqlite','.sqlite3')): continue
    if SECRET_NAMES.search(p.name) and not p.name.endswith('.example'): findings.append(f'secret-like filename: {rel}'); continue
    if p.suffix not in ALLOW_SUFFIX and p.name not in {'VERSION','Dockerfile','.gitignore'}: continue
    try: text=p.read_text(errors='ignore')
    except Exception: text=''
    if not p.name.endswith('.example'):
        for pat in SECRET_PATTERNS:
            if pat.search(text): findings.append(f'possible secret content: {rel}'); break
    files.append(p)
if findings:
    print('\n'.join(findings)); raise SystemExit(2)
OUT.parent.mkdir(parents=True,exist_ok=True)
with zipfile.ZipFile(OUT,'w',zipfile.ZIP_DEFLATED) as z:
    for p in files: z.write(p,p.relative_to(ROOT))
print(OUT); print(f'files={len(files)}')
