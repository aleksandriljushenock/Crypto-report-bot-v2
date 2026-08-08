"""Manual Telegram connectivity smoke test.

This file intentionally has no network side effects during pytest collection.
Run it explicitly with ``python test_telegram.py`` when credentials and network
access are available.
"""
import os


def main() -> None:
    import requests
    from dotenv import load_dotenv

    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID are required")

    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": "✅ Тестовое сообщение. Бот успешно подключен!"},
        timeout=15,
    )
    response.raise_for_status()
    print("Telegram connectivity OK")


if __name__ == "__main__":
    main()
