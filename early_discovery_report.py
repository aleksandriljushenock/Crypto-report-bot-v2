import html


def safe_html(value):
    return html.escape(
        str(value or ""),
        quote=True,
    )


def add_project_block(lines, index, item, rejected=False):
    discovery = item.get("discovery", {})
    research = item.get("research", {})
    security = item.get("security", {})
    prelisting = item.get("prelisting", {})
    alpha = item.get("alphaV3", item.get("alphaV2", {}))
    intelligence = item.get("intelligence", alpha.get("intelligence", {}))

    symbol = discovery.get("symbol", "UNKNOWN")
    name = research.get("name") or discovery.get("projectName") or symbol
    score = alpha.get("score", prelisting.get("prelistingScore", 0))
    grade = alpha.get("grade", prelisting.get("grade", "N/A"))
    prefix = "❌" if rejected else "✅"

    lines.extend([
        "",
        f"<b>{prefix} {index}. {safe_html(name)} ({safe_html(symbol)})</b>",
        f"<b>Источник:</b> {safe_html(discovery.get('source', 'UNKNOWN'))}",
        f"<b>AI Alpha v3:</b> {score}/100 | Grade {safe_html(grade)}",
        f"<b>Уверенность:</b> {alpha.get('confidence', 0)}%",
        f"<b>Решение:</b> {safe_html(alpha.get('actionLabel', prelisting.get('actionLabel', 'N/A')))}",
    ])
    if alpha.get("primaryNarrative"):
        lines.append(f"<b>Нарратив:</b> {safe_html(alpha['primaryNarrative'])}")
    lines.append(f"<b>Fundamental:</b> {research.get('overallScore', 0)}/100")
    if security.get("score") is not None:
        lines.append(f"<b>Security:</b> {security.get('score')}/100 | {safe_html(security.get('riskLevel'))}")
    else:
        lines.append("<b>Security:</b> N/A")

    vc = intelligence.get("vc", {})
    if vc.get("investors"):
        lines.append("<b>VC:</b> " + ", ".join(safe_html(x.get("name")) for x in vc["investors"][:4]))
    unlocks = intelligence.get("unlocks", {})
    lines.append(f"<b>Unlock/Dilution:</b> {unlocks.get('score', 0)}/10")
    social = intelligence.get("social", {})
    if social.get("available"):
        lines.append(f"<b>Social/Dev momentum:</b> {social.get('score', 0)}/10")
    smart = intelligence.get("smartMoney", {})
    if smart.get("detected"):
        lines.append("<b>Smart money mentions:</b> " + ", ".join(safe_html(x) for x in smart["detected"][:4]))

    weighted = alpha.get("weightedComponents", {})
    if weighted:
        top = sorted(weighted.items(), key=lambda x: x[1], reverse=True)[:6]
        lines.append("<b>Главные компоненты:</b> " + "; ".join(f"{safe_html(k)} {v}" for k, v in top))

    positives = alpha.get("reasonsFor", prelisting.get("reasonsFor", []))
    risks = alpha.get("reasonsAgainst", prelisting.get("reasonsAgainst", []))
    if positives:
        lines.append("<b>Плюсы:</b>")
        lines.extend(f"✓ {safe_html(x)}" for x in positives[:3])
    if risks:
        lines.append("<b>Почему не прошёл:</b>" if rejected else "<b>Риски:</b>")
        lines.extend(f"✕ {safe_html(x)}" for x in risks[:4])

    if alpha.get("adaptiveWeights", {}).get("learned"):
        lines.append(f"<b>Самообучение:</b> веса обучены на {alpha['adaptiveWeights'].get('samples', 0)} исходах")
    else:
        lines.append(f"<b>Самообучение:</b> сбор истории ({alpha.get('adaptiveWeights', {}).get('samples', 0)}/30 исходов)")

    url = discovery.get("url")
    if url:
        lines.append(f'<a href="{safe_html(url)}">Источник</a>')
    lines.append("━━━━━━━━━━━━━━━━━━━━")


def build_early_discovery_report(result):
    interesting = result.get("interesting", [])
    rejected = result.get("topRejected", [])
    stats = result.get("stats", {})
    learning = result.get("learning", {})
    lines = [
        "<b>🔭 EARLY DISCOVERY ENGINE v3</b>", "",
        f"Получено от источников: <b>{result.get('discoveredNow', 0)}</b>",
        f"Новых записей: <b>{result.get('newRowsNow', 0)}</b>",
        f"Проанализировано сейчас: <b>{result.get('analyzedNow', 0)}</b>",
        f"Всего в базе: <b>{stats.get('total', 0)}</b>",
        f"Интересных: <b>{stats.get('interesting', 0)}</b>",
        f"История обучения: <b>{learning.get('predictions', 0)}</b> прогнозов / <b>{learning.get('outcomes', 0)}</b> исходов",
        "", "<b>Источники:</b>",
    ]
    for source in result.get("sources", []):
        if source.get("error"):
            lines.append(f"⚠️ {safe_html(source.get('source'))}: {safe_html(source.get('error'))}")
        elif source.get("message"):
            lines.append(f"• {safe_html(source.get('source'))}: {safe_html(source.get('message'))}")
        else:
            lines.append(f"✓ {safe_html(source.get('source'))}: {source.get('count', 0)}")

    if interesting:
        lines.extend(["", "<b>🎯 ЛУЧШИЕ РАННИЕ ПРОЕКТЫ</b>"])
        for i, item in enumerate(interesting[:7], 1):
            add_project_block(lines, i, item, False)
    else:
        lines.extend(["", "<b>Сильных проектов сейчас нет.</b>"])

    if rejected:
        lines.extend(["", "<b>🔎 БЛИЖЕ ВСЕХ К ФИЛЬТРУ</b>"])
        for i, item in enumerate(rejected[:5], 1):
            add_project_block(lines, i, item, True)

    lines.extend(["", "<b>Важно:</b> VC, unlock и smart-money без специализированного провайдера оцениваются консервативно. Это снижает уверенность, а не превращается в выдуманный положительный сигнал."])
    return "\n".join(lines)
