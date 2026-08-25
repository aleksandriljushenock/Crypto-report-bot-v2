"""Compatibility entrypoint. Legacy sklearn champion.pkl training was retired in V42.

The production learner is Learning Engine V14; keeping this module as a facade
prevents old cron/jobs from silently training an unused model.
"""
from __future__ import annotations
import json
from ai_score_engine import DEFAULT_WEIGHTS
from learning_engine_v14 import train as train_v14

def train():
    from model_training_coordinator import training_slot
    with training_slot() as acquired:
        if not acquired:
            return {"status": "already-running"}
        return train_v14(DEFAULT_WEIGHTS)

def main():
    print(json.dumps(train(), ensure_ascii=False, default=str))

if __name__ == "__main__":
    main()
