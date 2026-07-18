import os
import sys
import requests
from pathlib import Path
from dotenv import load_dotenv

MAX_MESSAGE_LENGTH = 3900


def split_message(text, max_length=MAX_MESSAGE_LENGTH):
    parts = []

    while len(text) > max_length:
        cut = text.rfind("\n", 0, max_length)

        if cut == -1:
            cut = max_length

        parts.append(text[:cut])
        text = text[cut:].lstrip()

    if text:
        parts.append(text)

    return parts


def send_telegram_message(token, chat_id, text, html_mode=True):
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }

    if html_mode:
        payload["parse_mode"] = "HTML"

    response = requests.post(url, json=payload, timeout=20)

    if response.status_code != 200 and html_mode:
        payload.pop("parse_mode", None)
        response = requests.post(url, json=payload, timeout=20)

    response.raise_for_status()


def main():
    load_dotenv()

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if len(sys.argv) < 2:
        print("Usage: python telegram_sender.py data\\report_YYYYMMDD_HHMMSS.txt")
        return

    report_path = Path(sys.argv[1])
    text = report_path.read_text(encoding="utf-8")

    messages = text.split("===MESSAGE_BREAK===")

    for msg_index, message in enumerate(messages, start=1):
        message = message.strip()

        if not message:
            continue

        for part_index, part in enumerate(split_message(message), start=1):
            if part_index > 1:
                part = f"<b>Часть {msg_index}.{part_index}</b>\n\n{part}"

            send_telegram_message(token, chat_id, part, html_mode=True)

    print("Отчет отправлен в Telegram")


if __name__ == "__main__":
    main()