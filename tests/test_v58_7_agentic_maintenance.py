from pathlib import Path

def test_agentic_files_present():
    for rel in ['AGENTS.md','agent/policy.yml','.github/workflows/ci.yml','.github/workflows/agent-maintenance.yml','.github/workflows/agent-auto-merge.yml','.github/workflows/release.yml','.github/workflows/deploy-vps.yml','scripts/agent_audit.py','scripts/profitability_gate.py','scripts/vps_post_deploy_check.sh']:
        assert Path(rel).exists(), rel

def test_agent_contract_has_safety_boundaries():
    text=Path('AGENTS.md').read_text()
    assert 'Never push directly to `main`' in text
    assert 'Never weaken PF/expectancy/CI/drawdown/sample-size gates' in text
    assert 'future-data leakage' in text

def test_auto_merge_protects_sensitive_paths():
    text=Path('.github/workflows/agent-auto-merge.yml').read_text()
    for needle in ['execution_model_v57','paper_trading','cloud_model_store','.github/workflows','docker-compose']:
        assert needle in text

def test_release_builder_uses_v587_name():
    assert 'Crypto-report-bot-v58.7.0-agentic-maintenance.zip' in Path('scripts/build_release.py').read_text()
