# Crypto-report-bot agent operating contract

## Mission
Improve production reliability and untouched out-of-sample profitability without weakening safety, validation, or data-leakage protections.

## Non-negotiable rules
- Never claim profitability from training/selection metrics alone. Promotion decisions require untouched chronological OOS evidence.
- Never weaken PF/expectancy/CI/drawdown/sample-size gates merely to obtain a champion.
- Never introduce future-data leakage. Keep purge/embargo and tune/evidence/test separation.
- Never commit secrets, `.env`, runtime databases, logs, model binaries, credentials, API keys or private diagnostic payloads.
- Never push directly to `main`. Work on `agent/<issue>-<short-name>` and open a PR.
- Every bug fix needs a regression test reproducing the failure when feasible.
- Preserve fail-closed behavior for execution, cloud model publication, and deployment health checks.
- Do not enable live trading or increase leverage as part of an automated repair.

## Required verification before PR
1. `python -m compileall -q .`
2. `pytest -q`
3. `python scripts/agent_audit.py --strict`
4. `python scripts/build_release.py /tmp/crypto-report-bot-agent-check.zip`
5. If execution-model logic changed: include a diagnostic/replay result and explain OOS impact. Never use a single holdout as sole evidence.

## Protected areas
Changes to these require explicit human review and MUST NOT receive the `agent-auto-merge` label:
- `.github/workflows/**`
- `docker-compose*.yml`, `Dockerfile*`
- `migrations/**`, `*.sql`
- authentication, secrets, cloud credentials, SSH/deploy logic
- live-order execution, leverage, liquidation or capital sizing logic
- profitability gates / promotion thresholds

## PR format
State: root cause, files changed, regression test, test results, risk, rollback. For ML changes also state train/tune/evidence/OOS boundaries and whether any threshold/gate changed.
