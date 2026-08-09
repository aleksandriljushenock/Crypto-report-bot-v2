from types import SimpleNamespace

from telegram_ui import router


def _bot(sent):
    def send_message(chat_id, text, reply_markup=None, parse_mode="HTML"):
        sent.append((chat_id, text, reply_markup))
        return {"ok": True}

    def handle_command(chat_id, text):
        sent.append((chat_id, f"HANDLED:{text}", None))

    return SimpleNamespace(
        log=lambda *args, **kwargs: None,
        is_authorized=lambda chat_id: True,
        send_message=send_message,
        handle_command=handle_command,
        strategy_edit_pending={},
    )


def test_slash_start_reaches_command_handler_without_router_nameerror():
    sent = []
    bot = _bot(sent)
    router.process_update({"message": {"chat": {"id": 123}, "text": "/start"}}, bot)
    assert any(item[1] == "HANDLED:/start" for item in sent)


def test_slash_help_reaches_command_handler_without_router_nameerror():
    sent = []
    bot = _bot(sent)
    router.process_update({"message": {"chat": {"id": 123}, "text": "/help"}}, bot)
    assert any(item[1] == "HANDLED:/help" for item in sent)


def test_start_is_registered_in_bot_command_menu():
    from telegram_ui.commands import BOT_COMMANDS
    commands = [item["command"] for item in BOT_COMMANDS]
    assert "start" in commands
    assert "help" in commands
