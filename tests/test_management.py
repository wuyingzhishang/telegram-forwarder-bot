import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.ext import CommandHandler

from forwarder.bot import build_application
from forwarder.management import feature_command, menu_callback, send_main_menu


class FakeMessage:
    def __init__(self):
        self.calls = []

    async def reply_text(self, text, **kwargs):
        self.calls.append((text, kwargs))


def test_start_menu_shows_management_actions_only_to_admins(monkeypatch):
    monkeypatch.delenv("DASHBOARD_URL", raising=False)

    async def render(user_id):
        message = FakeMessage()
        update = SimpleNamespace(
            effective_message=message,
            effective_user=SimpleNamespace(id=user_id),
        )
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"admin_ids": {7}}),
            user_data={"managed_flow": {"kind": "rule", "step": "source_chat"}},
        )
        await send_main_menu(update, context)
        assert "managed_flow" not in context.user_data
        return message.calls[0]

    admin_text, admin_options = asyncio.run(render(7))
    user_text, user_options = asyncio.run(render(99))
    admin_callbacks = {
        button.callback_data
        for row in admin_options["reply_markup"].inline_keyboard
        for button in row
        if button.callback_data
    }
    user_callbacks = {
        button.callback_data
        for row in user_options["reply_markup"].inline_keyboard
        for button in row
        if button.callback_data
    }

    assert "管理菜单" in admin_text
    assert "menu:advanced" in admin_callbacks
    assert "menu:advanced" not in user_callbacks
    assert "将你有权转发" in user_text
    assert user_callbacks == {"menu:manual", "menu:publisher_set:bot", "menu:publisher_set:user", "menu:publisher", "menu:default_target", "menu:submit", "menu:tasks", "menu:help"}


@pytest.mark.parametrize("entry", ["/botforward", "/photo", "menu:new_bot", "menu:new_photo", "menu:photo_submit"])
@pytest.mark.parametrize("user_id", [7, 99])
def test_direct_entries_exit_source_wizard_without_requiring_login(entry, user_id):
    message = FakeMessage()
    message.text = entry
    query = SimpleNamespace(data=entry, message=message, answer=AsyncMock())
    update = SimpleNamespace(
        effective_message=message, effective_user=SimpleNamespace(id=user_id), callback_query=query,
    )
    context = SimpleNamespace(
        application=SimpleNamespace(bot_data={"admin_ids": {7}, "store": None, "user_session_enabled": False}),
        user_data={"managed_flow": {"kind": "rule", "step": "source_chat"}},
    )

    asyncio.run(menu_callback(update, context) if entry.startswith("menu:") else feature_command(update, context))

    assert "managed_flow" not in context.user_data
    assert len(message.calls) == 1
    assert "无需来源 ID" in message.calls[0][0]
    assert "默认转发对象" in message.calls[0][0]


def test_application_registers_realtime_and_history_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123456:ABCDEF")
    monkeypatch.setenv("ADMIN_IDS", "7")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "relay.sqlite3"))

    application = build_application()
    commands = {
        command
        for handlers in application.handlers.values()
        for handler in handlers
        if isinstance(handler, CommandHandler)
        for command in handler.commands
    }

    assert {"newrule", "history", "botforward", "userforward", "photo", "extract", "tasks", "defaulttarget", "account"} <= commands
