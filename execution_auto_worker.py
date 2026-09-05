#!/usr/bin/env python3
"""Isolated autonomous execution-learning worker for v58.6.3.

Owns the cross-process/distributed training lease itself, reports progress to a
small JSON file, and performs backfill -> train -> diagnose -> verified publish.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

RESULT_PATH = Path(os.getenv('EXECUTION_AUTO_RESULT_PATH', 'data/execution_auto_result.json'))
DIAG_PATH = Path(os.getenv('EXECUTION_AUTO_DIAGNOSTIC_PATH', 'data/execution_v58_6_3_latest_diagnostic.json'))
PROGRESS_PATH = Path(os.getenv('EXECUTION_AUTO_PROGRESS_PATH', 'data/execution_auto_progress.json'))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write(payload: dict) -> None:
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def _progress(stage: str, **extra) -> None:
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload={'stage':stage,'updated_at':_now(),'pid':os.getpid(),**extra}
    PROGRESS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print('[execution-auto] ' + ' '.join(f'{k}={v}' for k,v in payload.items()), flush=True)


def main() -> int:
    started = _now()
    try:
        from model_training_coordinator import training_slot
        _progress('lease-wait')
        with training_slot(owner='execution-auto-v58.6.3') as acquired:
            if not acquired:
                payload={'status':'training-slot-busy','started_at':started,'finished_at':_now()}
                _write(payload); _progress('lease-busy'); return 4

            from backfill_execution_dataset_v57 import backfill
            from execution_model_v57 import diagnose, train

            limit = max(1, min(30000, int(float(os.getenv('EXECUTION_AUTO_BACKFILL_ROWS', '20000')))))
            _progress('backfill', limit=limit)
            backfill_result = backfill(limit=limit, dry_run=False)
            if backfill_result.get('errors'):
                payload={'status':'backfill-error','started_at':started,'backfill':backfill_result,'finished_at':_now()}
                _write(payload); _progress('backfill-error', errors=backfill_result.get('errors')); return 2

            _progress('train')
            training = train(trigger='scheduled-auto-subprocess')

            _progress('diagnose')
            diagnostic = diagnose()
            DIAG_PATH.parent.mkdir(parents=True, exist_ok=True)
            DIAG_PATH.write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2, default=str), encoding='utf-8')

            breakout = (diagnostic.get('groups') or {}).get('BREAKOUT|LONG') or {}
            top = (breakout.get('top_selection') or [None])[0] or {}
            auto_analysis = {
                'breakout_healthy': breakout.get('healthy_outcome'), 'breakout_champion': breakout.get('champion_outcome'),
                'auc': top.get('auc'), 'champion_auc': top.get('champion_auc'), 'champion_pf': top.get('champion_return_pf'),
                'wf_ok': top.get('walk_forward_ok'), 'wf_reason': top.get('walk_forward_reason'),
                'wf_trades': top.get('walk_forward_total_trades'), 'wf_pf': top.get('walk_forward_aggregate_pf'),
                'wf_expectancy': top.get('walk_forward_aggregate_expectancy'), 'wf_drawdown': top.get('walk_forward_aggregate_max_drawdown'),
                'wf_ci_low': top.get('walk_forward_expectancy_ci_low'), 'gate_failures': top.get('gate_failures'),
            }
            payload = {
                'status': training.get('status'), 'version': training.get('version'), 'rows': training.get('rows'),
                'trained_models': training.get('trained_models'), 'healthy_models': training.get('healthy_models'),
                'champion_models': training.get('champion_models'), 'cloud_saved': training.get('cloud_saved'),
                'cloud_error': training.get('cloud_error'), 'label_quality': training.get('label_quality'),
                'auto_analysis': auto_analysis,
                'backfill': {k:backfill_result.get(k) for k in ('status','shadow_written','paper_written','unresolved','ambiguous','errors')},
                'diagnostic_path': str(DIAG_PATH), 'finished_at': _now(),
            }
            _write(payload); _progress('done', status=payload.get('status'))
            return 0 if training.get('status') not in {'error','invalid-label-balance','insufficient-data'} else 3
    except Exception as exc:
        payload={'status':'error','error':f'{type(exc).__name__}: {exc}','traceback':traceback.format_exc()[-6000:],
                 'started_at':started,'finished_at':_now()}
        _write(payload); _progress('error', error=payload['error']); print(payload['traceback'],file=sys.stderr,flush=True); return 1


if __name__ == '__main__':
    raise SystemExit(main())
