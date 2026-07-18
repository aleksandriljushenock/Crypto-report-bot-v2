import requests


GOPLUS_BASE_URL = "https://api.gopluslabs.io/api/v1"
REQUEST_TIMEOUT = 20


PLATFORM_CHAIN_MAP = {
    "ethereum": "1",
    "binance-smart-chain": "56",
    "polygon-pos": "137",
    "arbitrum-one": "42161",
    "optimistic-ethereum": "10",
    "avalanche": "43114",
    "base": "8453",
    "fantom": "250",
    "cronos": "25",
    "gnosis": "100",
    "mantle": "5000",
    "linea": "59144",
    "zksync": "324",
}


def is_truthy_flag(value):
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


def request_json(url, params=None):
    response = requests.get(
        url,
        params=params,
        timeout=REQUEST_TIMEOUT,
        headers={
            "accept": "application/json",
            "User-Agent": "Crypto-Research-Service",
        },
    )

    response.raise_for_status()
    return response.json()


def choose_contract(contract_addresses):
    if not isinstance(contract_addresses, dict):
        return None

    priority = [
        "ethereum",
        "binance-smart-chain",
        "base",
        "arbitrum-one",
        "optimistic-ethereum",
        "polygon-pos",
        "avalanche",
        "solana",
        "sui",
    ]

    for platform in priority:
        address = contract_addresses.get(platform)

        if address:
            return {
                "platform": platform,
                "address": address,
            }

    for platform, address in contract_addresses.items():
        if address:
            return {
                "platform": platform,
                "address": address,
            }

    return None


def fetch_evm_security(chain_id, address):
    data = request_json(
        f"{GOPLUS_BASE_URL}/token_security/{chain_id}",
        params={
            "contract_addresses": address,
        },
    )

    result = data.get("result", {})
    return result.get(address.lower()) or result.get(address)


def fetch_solana_security(address):
    data = request_json(
        f"{GOPLUS_BASE_URL}/solana/token_security",
        params={
            "contract_addresses": address,
        },
    )

    result = data.get("result", {})
    return result.get(address)


def fetch_sui_security(address):
    data = request_json(
        f"{GOPLUS_BASE_URL}/sui/token_security",
        params={
            "contract_addresses": address,
        },
    )

    result = data.get("result", {})
    return result.get(address)


def collect_token_security(coingecko_details):
    contracts = coingecko_details.get(
        "contractAddresses",
        {},
    )

    selected = choose_contract(contracts)

    if not selected:
        return {
            "available": False,
            "error": "Адрес контракта не найден",
        }

    platform = selected["platform"]
    address = selected["address"]

    try:
        if platform == "solana":
            raw = fetch_solana_security(address)

        elif platform == "sui":
            raw = fetch_sui_security(address)

        else:
            chain_id = PLATFORM_CHAIN_MAP.get(platform)

            if not chain_id:
                return {
                    "available": False,
                    "platform": platform,
                    "address": address,
                    "error": "Сеть пока не поддерживается",
                }

            raw = fetch_evm_security(
                chain_id,
                address,
            )

        if not raw:
            return {
                "available": False,
                "platform": platform,
                "address": address,
                "error": "GoPlus не вернул данные",
            }

        return {
            "available": True,
            "platform": platform,
            "address": address,
            "raw": raw,
        }

    except requests.RequestException as exc:
        return {
            "available": False,
            "platform": platform,
            "address": address,
            "error": str(exc),
        }