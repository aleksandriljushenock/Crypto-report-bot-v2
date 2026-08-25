"""Telegram transport only. No trading/business logic."""
from __future__ import annotations
import re
from core.http_client import http
from core.logging_setup import get_logger
from core.settings import settings

log = get_logger("telegram_transport")
TELEGRAM_MESSAGE_LIMIT = 3900


def telegram_request(method, payload=None, timeout=40):
    token = settings.telegram_bot_token
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN отсутствует в переменных окружения")
    url = f"https://api.telegram.org/bot{token}/{method}"
    response = http.post(url, json=payload or {}, timeout=(settings.http_connect_timeout, timeout), raise_for_status=False)
    if not response.ok:
        try:
            error_data = response.json()
        except Exception:
            error_data = {"raw": response.text}
        raise RuntimeError(f"Telegram API error {response.status_code}: {error_data}")
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API returned ok=false: {data}")
    return data.get("result")


def split_telegram_message(text, limit=TELEGRAM_MESSAGE_LIMIT):
    """Split Telegram HTML without cutting tags/entities; balance simple formatting tags."""
    text=str(text or "").strip()
    if not text:
        return []
    token_re=re.compile(r"(<[^>]+>|&[A-Za-z0-9#]+;|\n|[^<&\n]+|[<&])")
    tokens=token_re.findall(text)
    parts=[]; cur=""; stack=[]
    tag_re=re.compile(r"^<(/?)(b|strong|i|em|u|s|code|pre)(?:\s[^>]*)?>$",re.I)
    def closing(): return "".join(f"</{t}>" for t in reversed(stack))
    def opening(): return "".join(f"<{t}>" for t in stack)
    for tok in tokens:
        m=tag_re.match(tok)
        projected=len(cur)+len(tok)+len(closing())
        if cur and projected>limit:
            parts.append((cur+closing()).strip()); cur=opening()
        max_piece=max(1,limit-len(closing())-len(opening()))
        if len(tok)>max_piece:
            for i in range(0,len(tok),max_piece):
                piece=tok[i:i+max_piece]
                if cur and len(cur)+len(piece)+len(closing())>limit:
                    parts.append((cur+closing()).strip()); cur=opening()
                cur+=piece
        else:
            cur+=tok
        if m:
            is_close=bool(m.group(1)); tag=m.group(2).lower()
            if is_close:
                if tag in stack:
                    idx=len(stack)-1-stack[::-1].index(tag); stack.pop(idx)
            else:
                stack.append(tag)
    if cur.strip(): parts.append((cur+closing()).strip())
    return [p for p in parts if p]


def send_message(chat_id, text, reply_markup=None, parse_mode="HTML"):
    parts = split_telegram_message(text)
    if not parts:
        log.warning("Попытка отправить пустое сообщение")
        return None
    responses = []
    for index, part in enumerate(parts):
        payload = {"chat_id": chat_id, "text": part, "disable_web_page_preview": True}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup is not None and index == len(parts) - 1:
            payload["reply_markup"] = reply_markup
        try:
            responses.append(telegram_request("sendMessage", payload, timeout=30))
        except Exception as exc:
            log.warning("Telegram send failed part=%s/%s: %s", index + 1, len(parts), exc)
            fallback = {
                "chat_id": chat_id,
                "text": re.sub(r"<[^>]+>", "", part),
                "disable_web_page_preview": True,
            }
            if reply_markup is not None and index == len(parts) - 1:
                fallback["reply_markup"] = reply_markup
            responses.append(telegram_request("sendMessage", fallback, timeout=30))
    return responses[0] if len(responses) == 1 else responses


def set_webhook(webhook_url, secret_token=None, drop_pending_updates=False):
    payload = {
        "url": str(webhook_url).rstrip("/"),
        "allowed_updates": ["message", "callback_query"],
        "drop_pending_updates": bool(drop_pending_updates),
    }
    if secret_token:
        payload["secret_token"] = str(secret_token)
    return telegram_request("setWebhook", payload, timeout=30)


def delete_webhook(drop_pending_updates=False):
    return telegram_request("deleteWebhook", {"drop_pending_updates": bool(drop_pending_updates)}, timeout=20)


def get_webhook_info():
    return telegram_request("getWebhookInfo", {}, timeout=20)
