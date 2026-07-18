def is_true(value):
    return str(value).lower() in {
        "1",
        "true",
        "yes",
    }


def safe_float(value, default=None):
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def analyze_token_security(security_data):
    if not security_data.get("available"):
        return {
            "available": False,
            "score": None,
            "riskLevel": "UNKNOWN",
            "hardReject": False,
            "risks": [],
            "warnings": [
                security_data.get(
                    "error",
                    "Проверка безопасности недоступна",
                )
            ],
        }

    raw = security_data.get("raw", {})

    score = 100
    hard_reject = False
    risks = []
    warnings = []
    positives = []

    critical_flags = {
        "is_honeypot": "Возможный honeypot",
        "is_blacklisted": "Есть blacklist-механика",
        "cannot_sell_all": "Нельзя продать весь объём",
        "selfdestruct": "Контракт может быть уничтожен",
        "hidden_owner": "Скрытый владелец",
        "owner_change_balance": (
            "Владелец может изменять балансы"
        ),
    }

    for field, message in critical_flags.items():
        if is_true(raw.get(field)):
            hard_reject = True
            score -= 40
            risks.append(message)

    dangerous_flags = {
        "is_mintable": "Возможна дополнительная эмиссия",
        "can_take_back_ownership": (
            "Можно вернуть права владельца"
        ),
        "transfer_pausable": (
            "Переводы могут быть остановлены"
        ),
        "trading_cooldown": (
            "Есть ограничение частоты торговли"
        ),
        "personal_slippage_modifiable": (
            "Комиссии отдельных адресов изменяемы"
        ),
        "slippage_modifiable": (
            "Торговые комиссии могут изменяться"
        ),
        "is_proxy": "Контракт использует proxy",
    }

    for field, message in dangerous_flags.items():
        if is_true(raw.get(field)):
            score -= 12
            risks.append(message)

    buy_tax = safe_float(raw.get("buy_tax"))
    sell_tax = safe_float(raw.get("sell_tax"))

    if buy_tax is not None:
        buy_tax_percent = buy_tax * 100

        if buy_tax_percent > 10:
            score -= 20
            risks.append(
                f"Высокий buy tax: {buy_tax_percent:.1f}%"
            )
        elif buy_tax_percent > 3:
            score -= 7
            warnings.append(
                f"Buy tax: {buy_tax_percent:.1f}%"
            )

    if sell_tax is not None:
        sell_tax_percent = sell_tax * 100

        if sell_tax_percent > 10:
            score -= 30
            risks.append(
                f"Высокий sell tax: {sell_tax_percent:.1f}%"
            )
        elif sell_tax_percent > 3:
            score -= 10
            warnings.append(
                f"Sell tax: {sell_tax_percent:.1f}%"
            )

    holder_count = safe_float(
        raw.get("holder_count")
    )

    if holder_count is not None:
        if holder_count < 100:
            score -= 15
            risks.append(
                "Очень мало держателей"
            )
        elif holder_count >= 10_000:
            score += 5
            positives.append(
                "Широкая база держателей"
            )

    if is_true(raw.get("is_open_source")):
        positives.append("Контракт open source")
    else:
        score -= 10
        warnings.append(
            "Исходный код не подтверждён"
        )

    score = max(0, min(100, score))

    if hard_reject or score < 35:
        risk_level = "CRITICAL"
    elif score < 55:
        risk_level = "HIGH"
    elif score < 75:
        risk_level = "MEDIUM"
    else:
        risk_level = "LOW"

    return {
        "available": True,
        "score": score,
        "riskLevel": risk_level,
        "hardReject": hard_reject,
        "platform": security_data.get("platform"),
        "address": security_data.get("address"),
        "positives": positives[:5],
        "risks": risks[:10],
        "warnings": warnings[:10],
        "metrics": {
            "buyTax": buy_tax,
            "sellTax": sell_tax,
            "holderCount": holder_count,
        },
    }