import os
import threading
import time
from datetime import datetime, timezone

from automation_store import claim_notification, get_service_states, save_service_state
from early_discovery_pipeline import run_early_discovery
from early_discovery_report import add_project_block
from listing_pipeline import run_incremental_listing_scan
from outcome_tracker import update_due_outcomes
from trade_outcome_tracker import update_trade_outcomes
from capital_flow_engine import scan_capital_flows
from news_engine import scan_news
from narrative_engine import scan_narratives
from smart_money_engine import scan_smart_money
from self_learning_engine import retrain
from ai_intelligence import run_ai_intelligence, build_signal_ai_block
from ai_score_engine import claim_ai_alert


from core.scheduler import PeriodicWorker


class AutomationSupervisor:
    def __init__(self, sender, logger, chat_id):
        self.sender = sender
        self.logger = logger
        self.chat_id = str(chat_id) if chat_id else None
        self.workers = []
        self._build_workers()

    @staticmethod
    def _bool_env(name, default=True):
        value = os.getenv(name)
        if value is None:
            return default
        return value.strip().lower() in {'1', 'true', 'yes', 'on'}

    @staticmethod
    def _minutes(name, default):
        try:
            return max(1, int(os.getenv(name, str(default))))
        except ValueError:
            return default

    def _build_workers(self):
        discovery_minutes = self._minutes('DISCOVERY_MONITOR_INTERVAL_MINUTES', 30)
        listing_minutes = self._minutes('LISTING_REFRESH_INTERVAL_MINUTES', 360)
        outcome_minutes = self._minutes('OUTCOME_UPDATE_INTERVAL_MINUTES', 180)
        trade_outcome_minutes = self._minutes('TRADE_OUTCOME_UPDATE_INTERVAL_MINUTES', 60)
        capital_flow_minutes = self._minutes('CAPITAL_FLOW_INTERVAL_MINUTES', 15)
        news_minutes = self._minutes('NEWS_INTERVAL_MINUTES', 10)
        narrative_minutes = self._minutes('NARRATIVE_INTERVAL_MINUTES', 60)
        smart_money_minutes = self._minutes('SMART_MONEY_INTERVAL_MINUTES', 20)
        learning_minutes = self._minutes('SELF_LEARNING_INTERVAL_MINUTES', 360)
        ai_minutes = self._minutes('AI_INTELLIGENCE_INTERVAL_MINUTES', 20)
        self.workers = [
            PeriodicWorker(
                'early-discovery-monitor', discovery_minutes * 60,
                self._run_discovery, self.logger,
                enabled=self._bool_env('DISCOVERY_MONITOR_ENABLED', True), first_delay=20,
            ),
            PeriodicWorker(
                'listing-database-refresh', listing_minutes * 60,
                self._run_listing_refresh, self.logger,
                enabled=self._bool_env('LISTING_REFRESH_ENABLED', True), first_delay=60,
            ),
            PeriodicWorker(
                'trade-outcome-tracker', trade_outcome_minutes * 60,
                self._run_trade_outcomes, self.logger,
                enabled=self._bool_env('TRADE_OUTCOME_TRACKER_ENABLED', True), first_delay=120,
            ),
            PeriodicWorker(
                'outcome-tracker', outcome_minutes * 60,
                self._run_outcomes, self.logger,
                enabled=self._bool_env('OUTCOME_TRACKER_ENABLED', True), first_delay=90,
            ),
            PeriodicWorker(
                'capital-flow-engine', capital_flow_minutes * 60,
                self._run_capital_flows, self.logger,
                enabled=self._bool_env('CAPITAL_FLOW_ENABLED', True), first_delay=45,
            ),
            PeriodicWorker(
                'ai-news-engine', news_minutes * 60,
                self._run_news, self.logger,
                enabled=self._bool_env('NEWS_ENGINE_ENABLED', True), first_delay=75,
            ),
            PeriodicWorker(
                'narrative-engine', narrative_minutes * 60,
                self._run_narratives, self.logger,
                enabled=self._bool_env('NARRATIVE_ENGINE_ENABLED', True), first_delay=105,
            ),
            PeriodicWorker(
                'smart-money-engine', smart_money_minutes * 60,
                self._run_smart_money, self.logger,
                enabled=self._bool_env('SMART_MONEY_ENABLED', True), first_delay=135,
            ),
            PeriodicWorker(
                'ai-intelligence-engine', ai_minutes * 60,
                self._run_ai_intelligence, self.logger,
                enabled=self._bool_env('AI_INTELLIGENCE_ENABLED', True), first_delay=150,
            ),
            PeriodicWorker(
                'self-learning-engine', learning_minutes * 60,
                self._run_learning, self.logger,
                enabled=self._bool_env('SELF_LEARNING_ENABLED', True), first_delay=180,
            ),
        ]

    def start(self):
        started = 0
        for worker in self.workers:
            started += int(worker.start())
        self.logger(f'Автосервисы запущены: {started}/{len(self.workers)}')
        return started

    def stop(self):
        for worker in self.workers:
            worker.stop(join_timeout=5.0)

    def status(self):
        runtime = {w.name: {'enabled': w.enabled, 'alive': w.alive(), 'intervalMinutes': round(w.interval_seconds / 60)} for w in self.workers}
        stored = {row['service']: row for row in get_service_states()}
        return {'runtime': runtime, 'stored': stored}

    def _run_discovery(self):
        limit = int(os.getenv('DISCOVERY_ANALYSIS_LIMIT', '20'))
        result = run_early_discovery(analysis_limit=max(1, limit))
        min_score = float(os.getenv('DISCOVERY_NOTIFY_MIN_SCORE', '75'))
        sent = 0
        for item in result.get('interesting', []):
            alpha = item.get('alphaV3', {})
            score = float(alpha.get('score') or 0)
            if score < min_score:
                continue
            discovery = item.get('discovery', {})
            symbol = discovery.get('symbol') or 'UNKNOWN'
            key = f"discovery:{symbol}:{round(score)}:{alpha.get('action')}"
            if not claim_notification(key, 'discovery', item):
                continue
            lines = ['<b>🔭 НОВЫЙ СИЛЬНЫЙ ПРОЕКТ</b>']
            add_project_block(lines, 1, item, rejected=False)
            self.sender(self.chat_id, '\n'.join(lines))
            sent += 1
        self.logger(f"Early Discovery monitor: found={result.get('discoveredNow')}, analyzed={result.get('analyzedNow')}, sent={sent}")
        return {'found': result.get('discoveredNow', 0), 'analyzed': result.get('analyzedNow', 0), 'sent': sent}

    def _run_listing_refresh(self):
        limit = int(os.getenv('LISTING_REFRESH_ANALYSIS_LIMIT', '25'))
        result = run_incremental_listing_scan(deep_limit=max(1, limit))
        self.logger(f"Listing refresh: saved={result.get('binanceSymbolsSaved')}, analyzed={result.get('deepAnalyzedThisRun')}")
        return {
            'saved': result.get('binanceSymbolsSaved', 0),
            'analyzed': result.get('deepAnalyzedThisRun', 0),
            'interesting': result.get('interestingCount', 0),
        }

    def _run_trade_outcomes(self):
        result = update_trade_outcomes()
        self.logger(f"Trade outcomes: updated={result.get('updated')}, errors={len(result.get('errors', []))}")
        return result

    def _run_outcomes(self):
        result = update_due_outcomes()
        self.logger(f"Outcome tracker: updated={result.get('updated')}, errors={len(result.get('errors', []))}")
        return result

    def _run_capital_flows(self):
        items = scan_capital_flows(int(os.getenv('CAPITAL_FLOW_TOP_SYMBOLS', '25')))
        self.logger(f"Capital flows: analyzed={len(items)}")
        return {'analyzed': len(items), 'top': items[:3]}

    def _run_news(self):
        items = scan_news()
        threshold = float(os.getenv('NEWS_NOTIFY_MIN_IMPACT', '75'))
        sent = 0
        for item in items:
            if float(item.get('impact') or 0) < threshold or not self.chat_id:
                continue
            key = f"news:{item.get('title','')[:160]}"
            if claim_notification(key, 'news', item):
                self.sender(self.chat_id, f"<b>⚠️ ВАЖНАЯ НОВОСТЬ</b>\nImpact: <b>{item['impact']}</b>\n\n{item['title']}")
                sent += 1
        self.logger(f"AI news: new={len(items)}, sent={sent}")
        return {'new': len(items), 'sent': sent}

    def _run_narratives(self):
        items = scan_narratives()
        return {'count': len(items), 'top': items[:3]}

    def _run_smart_money(self):
        items = scan_smart_money()
        return {'count': len(items), 'top': items[:3]}

    def _run_ai_intelligence(self):
        result = run_ai_intelligence(max_results=int(os.getenv('AI_INTELLIGENCE_MAX_RESULTS', '15')))
        threshold = float(os.getenv('AI_ALERT_MIN_SCORE', '88'))
        sent = 0
        for signal in result.get('signals', []):
            if float(signal.get('aiScore') or 0) < threshold or not self.chat_id:
                continue
            if claim_ai_alert(signal, int(os.getenv('AI_ALERT_COOLDOWN_HOURS', '12'))):
                text = (
                    f"<b>🔥 AI ALERT v13 — {signal.get('symbol')}</b>\n\n"
                    + build_signal_ai_block(signal)
                    + f"\n\nНаправление: <b>{signal.get('direction')}</b>"
                    + f"\nRR: <b>{signal.get('rr')}</b> · Probability: <b>{signal.get('probability')}%</b>"
                )
                self.sender(self.chat_id, text)
                sent += 1
        return {'ranked': len(result.get('signals', [])), 'sent': sent, 'threshold': threshold}

    def _run_learning(self):
        return retrain()


def build_automation_status(supervisor):
    status = supervisor.status() if supervisor else {'runtime': {}, 'stored': {}}
    lines = ['<b>⚙️ АВТОСЕРВИСЫ</b>', '']
    labels = {
        'early-discovery-monitor': 'Early Discovery',
        'listing-database-refresh': 'База листингов',
        'trade-outcome-tracker': 'Trade Outcome Tracker',
        'outcome-tracker': 'Alpha Outcome Tracker',
        'capital-flow-engine': 'Capital Flow Engine',
        'ai-news-engine': 'AI News Engine',
        'narrative-engine': 'Narrative Engine',
        'smart-money-engine': 'Smart Money Engine',
        'ai-intelligence-engine': 'AI Intelligence v13',
        'self-learning-engine': 'Self Learning Engine',
    }
    for name, runtime in status['runtime'].items():
        saved = status['stored'].get(name, {})
        state = 'работает' if runtime.get('alive') else ('выключен' if not runtime.get('enabled') else 'не запущен')
        lines.append(f"<b>{labels.get(name, name)}</b>: {state}, каждые {runtime.get('intervalMinutes')} мин")
        if saved.get('last_success'):
            lines.append(f"Последний успех: {saved['last_success']}")
        if saved.get('last_error'):
            lines.append(f"Ошибка: <code>{saved['last_error'][:300]}</code>")
        lines.append('')
    return '\n'.join(lines).rstrip()
