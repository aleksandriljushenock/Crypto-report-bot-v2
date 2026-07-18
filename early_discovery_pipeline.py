from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from alpha_engine_v3 import calculate_alpha_v3
from early_discovery_collector import collect_all_discovery_sources
from early_discovery_database import (
    get_interesting_projects,
    get_pending_projects,
    get_stats,
    get_top_rejected_projects,
    initialize_database,
    mark_processing,
    reset_processing,
    save_analysis,
    save_analysis_error,
    save_discovered_project,
)
from intelligence_providers import collect_intelligence
from outcome_tracker import get_learning_stats, register_prediction, update_due_outcomes
from prelisting_score import calculate_prelisting_score
from research_analyzer import analyze_project_research
from research_collector import collect_project_research
from token_security_analyzer import analyze_token_security
from token_security_collector import collect_token_security


MAX_ANALYSIS_PER_RUN = 40
MAX_WORKERS = 2


def empty_security(message):
    return {
        "available": False,
        "score": None,
        "riskLevel": "UNKNOWN",
        "hardReject": False,
        "positives": [],
        "risks": [],
        "warnings": [message],
    }


def project_key(row):
    address = row.get("contract_address")
    if address:
        return f"contract:{str(address).lower()}"
    symbol = str(row.get("symbol") or "").upper()
    name = str(row.get("project_name") or "").lower().strip()
    return f"symbol:{symbol}|name:{name}"


def analyze_project_row(row):
    project_id = row["id"]
    symbol = row.get("symbol")
    project_name = row.get("project_name")
    mark_processing(project_id)

    try:
        raw_research = collect_project_research(symbol, project_name=project_name)
        research = analyze_project_research(raw_research)
        security = empty_security("Security-данные недоступны")
        coingecko = raw_research.get("coingecko", {}) or {}
        if coingecko:
            try:
                security = analyze_token_security(collect_token_security(coingecko))
            except Exception as exc:
                security = empty_security(f"Security error: {exc}")

        discovery = {
            "source": row.get("source"),
            "symbol": symbol,
            "projectName": project_name,
            "url": row.get("announcement_url"),
            "sourceAddedAt": row.get("source_added_at"),
            "contractPlatform": row.get("contract_platform"),
            "contractAddress": row.get("contract_address"),
        }
        key = project_key(row)
        intelligence = collect_intelligence(key, raw_research, research)
        alpha_v3 = calculate_alpha_v3(
            raw_research=raw_research,
            research=research,
            security=security,
            discovery=discovery,
            intelligence=intelligence,
        )
        prelisting = calculate_prelisting_score(research, security, discovery)
        prelisting.update({
            "prelistingScore": alpha_v3.get("score", 0),
            "grade": alpha_v3.get("grade", "F"),
            "interesting": alpha_v3.get("interesting", False),
            "action": alpha_v3.get("action", "SKIP"),
            "actionLabel": alpha_v3.get("actionLabel", "🔴 ПРОПУСТИТЬ"),
            "reasonsFor": alpha_v3.get("reasonsFor", []),
            "reasonsAgainst": alpha_v3.get("reasonsAgainst", []),
        })
        result = {
            "discovery": discovery,
            "research": research,
            "security": security,
            "prelisting": prelisting,
            "alphaV3": alpha_v3,
            "intelligence": intelligence,
        }
        save_analysis(project_id, result)

        market = coingecko.get("market", {}) or {}
        register_prediction(
            project_key=key,
            coin_id=coingecko.get("id"),
            symbol=symbol,
            score=alpha_v3.get("score", 0),
            components=alpha_v3.get("weightedComponents", {}),
            entry_price=market.get("priceUsd"),
        )
        return result
    except Exception as exc:
        save_analysis_error(project_id, exc)
        return {"discovery": {"source": row.get("source"), "symbol": symbol, "projectName": project_name}, "error": str(exc)}


def run_early_discovery(analysis_limit=MAX_ANALYSIS_PER_RUN):
    initialize_database()
    reset_processing()
    outcome_update = update_due_outcomes()
    collection = collect_all_discovery_sources()
    discovered_items = collection.get("items", [])

    inserted_now = 0
    for item in discovered_items:
        # sqlite rowcount is not reliable for UPSERT, so compare through the function's boolean only as a best effort.
        if save_discovered_project(item):
            inserted_now += 1

    pending = get_pending_projects(limit=analysis_limit)
    analyzed_now = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(analyze_project_row, row): row for row in pending}
        for future in as_completed(futures):
            try:
                analyzed_now.append(future.result())
            except Exception:
                pass

    stored = get_interesting_projects(limit=12)
    interesting = [item.get("analysis", {}) for item in stored if item.get("analysis")]
    interesting.sort(key=lambda item: item.get("alphaV3", {}).get("score", 0), reverse=True)

    rejected_rows = get_top_rejected_projects(limit=7)
    rejected = [item.get("analysis", {}) for item in rejected_rows if item.get("analysis")]
    rejected.sort(key=lambda item: item.get("alphaV3", {}).get("score", item.get("prelisting", {}).get("prelistingScore", 0)), reverse=True)

    return {
        "runAtUtc": datetime.now(timezone.utc).isoformat(),
        "discoveredNow": len(discovered_items),
        "newRowsNow": inserted_now,
        "analyzedNow": len(analyzed_now),
        "interesting": interesting,
        "interestingCount": len(interesting),
        "topRejected": rejected,
        "sources": collection.get("sources", []),
        "stats": get_stats(),
        "learning": get_learning_stats(),
        "outcomeUpdate": outcome_update,
    }
