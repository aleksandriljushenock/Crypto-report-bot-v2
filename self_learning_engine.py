"""Self-learning facade for v14 maximum-learning engine."""
from model_training_coordinator import training_slot

from ai_score_engine import DEFAULT_WEIGHTS
from learning_engine_v14 import diagnostics, train



def retrain():
    with training_slot(owner="self-learning-v14") as acquired:
        if not acquired:
            local = {"status": "already-running", "message": "another model training is already running"}
            return {"local": local, "cloudAdaptive": {"status": "skipped"}, "status": "already-running"}
        result = train(DEFAULT_WEIGHTS)
        try:
            from adaptive_cloud_learning import train_cloud_overlay
            cloud = train_cloud_overlay(DEFAULT_WEIGHTS)
        except Exception as exc:
            cloud = {"status": "error", "error": str(exc)}
        return {"local": result, "cloudAdaptive": cloud, "status": result.get("status") if isinstance(result, dict) else "done"}


def build_learning_report():
    # A report is read-only. Training is started only by the explicit Model Control
    # action or the scheduled learner; opening /learn/professional report must not mutate models.
    result = {"status": "report-only"}
    data = diagnostics(DEFAULT_WEIGHTS)
    active = data["active"]
    metrics = data["metrics"]
    drift = data.get("drift") or {}
    config = active.get("config") or {}
    lines = [
        "<b>🧬 AI SELF LEARNING MAX v14</b>", "",
        f"Активная модель: <b>{active['version']}</b>",
        f"Завершённых прогнозов: <b>{data['samples']}</b>",
        f"Режим: <b>диагностика без запуска обучения</b>",
        f"Рыночный drift: <b>{drift.get('status', 'n/a')}</b> ({float(drift.get('score', 0)):.3f})",
        f"Специалистов режимов: <b>{len((config.get('specialists') or {}))}</b>",
    ]
    if result.get("status") == "collecting-data":
        local = result.get("local") or {}
        lines.append(f"Нужно минимум: <b>{local.get('required', 'не указано')}</b>")
    if metrics.get("samples"):
        lines += [
            "", "<b>Качество на собственной истории:</b>",
            f"Win rate: <b>{metrics.get('overall_win_rate', 0)}%</b>",
            f"Средний результат: <b>{metrics.get('overall_avg_return', 0):+.2f}%</b>",
            f"Top 25% win rate: <b>{metrics.get('top_win_rate', 0)}%</b>",
            f"Top 25% return: <b>{metrics.get('top_avg_return', 0):+.2f}%</b>",
            f"Brier score: <b>{metrics.get('brier', 0):.4f}</b>",
            f"Rank correlation: <b>{metrics.get('rank_corr', 0):+.3f}</b>",
        ]
    regimes = data.get("regimes") or {}
    if regimes:
        lines += ["", "<b>Режимы рынка:</b>"]
        for name, m in sorted(regimes.items(), key=lambda x: x[1].get("top_avg_return", -999), reverse=True):
            lines.append(f"• {name}: n={m.get('samples',0)}, WR {m.get('overall_win_rate',0):.0f}%, top {m.get('top_avg_return',0):+.2f}%")
    rules = active.get("rules") or []
    if rules:
        lines += ["", f"<b>Активных выученных правил: {len(rules)}</b>"]
        for rule in rules[:6]:
            sign = "+" if float(rule.get("adjustment", 0)) >= 0 else ""
            extra = f" + {rule.get('feature2')}" if rule.get("feature2") else ""
            lines.append(f"• {rule.get('kind')}: {rule.get('feature')}{extra} → {sign}{float(rule.get('adjustment',0)):.1f}")
    lines += ["", "<i>Champion заменяется только после walk-forward проверки и отдельного holdout-теста.</i>"]
    return "\n".join(lines)


def build_model_status_report():
    data = diagnostics(DEFAULT_WEIGHTS)
    active = data["active"]
    versions = data.get("versions") or []
    lines = ["<b>🧠 MODEL LAB v14</b>", "", f"Champion: <code>{active['version']}</code>"]
    for row in versions[:6]:
        lines.append(f"• {row.get('version')} · {row.get('status')} · n={row.get('sample_count')}")
    return "\n".join(lines)
