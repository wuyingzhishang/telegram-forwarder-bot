import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from forwarder.store import Store
from forwarder.dashboard import create_app
from forwarder.bot import _offer_batch
from forwarder.management import menu_callback, send_main_menu


def test_account_expiry_is_fixed_and_independent_of_destination(tmp_path, monkeypatch):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE user_preferences(user_id INTEGER PRIMARY KEY, default_target_chat_id TEXT NOT NULL)")
        db.execute("INSERT INTO user_preferences VALUES(7, '-100200')")
    store = Store(str(path))
    store.add_target("-100200", "Archive")
    monkeypatch.setattr("forwarder.store.time.time", lambda: 1000)
    store.set_publishing_account(7, "bot")
    expiry = 1000 + 30 * 86400
    assert store.default_target(7) == "-100200"
    assert store.publishing_preference(8)["sender_account"] is None
    monkeypatch.setattr("forwarder.store.time.time", lambda: expiry - 1)
    assert Store(str(path)).publishing_preference(7)["sender_account"] == "bot"
    assert store.publishing_preference(7)["expires_at"] == expiry
    store.set_default_target(7, None)
    assert store.publishing_preference(7)["sender_account"] == "bot"
    monkeypatch.setattr("forwarder.store.time.time", lambda: expiry)
    assert store.publishing_preference(7) == {"sender_account": None, "expires_at": expiry, "expired": True}
    store.set_publishing_account(7, "user")
    store.set_default_target(7, "-100200")
    store.remove_target("-100200")
    assert store.publishing_preference(7)["sender_account"] == "user"
    store.set_publishing_account(7, None)
    assert store.publishing_preference(7)["expires_at"] is None


def test_publishing_api_auth_ownership_and_session(tmp_path, monkeypatch):
    path = str(tmp_path / "api.sqlite3")
    for key, value in {"DATABASE_PATH": path, "ADMIN_IDS": "7,8", "DASHBOARD_USERNAME": "test", "DASHBOARD_PASSWORD": "test", "USER_SESSION_ENABLED": "true"}.items():
        monkeypatch.setenv(key, value)
    store = Store(path)
    store.add_target("-100200", "Archive")
    async def check():
        async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
            assert (await client.get("/api/publishing")).status_code == 401
            client.auth = ("test", "test")
            assert (await client.put("/api/publishing/account", json={"sender_account": "bot"})).status_code == 403
            client.headers["X-Relay-Request"] = "dashboard"
            for session in (None, 8):
                store.set_user_session_account(session)
                assert (await client.put("/api/publishing/account", json={"sender_account": "user"})).status_code == 409
            store.set_user_session_account(7)
            response = await client.put("/api/publishing/account", json={"sender_account": "user"})
            assert response.status_code == 200
            assert response.json()["personal_available"] is True
            assert store.publishing_preference(7)["sender_account"] == "user"
            assert store.publishing_preference(8)["sender_account"] is None
            assert (await client.put("/api/publishing/target", json={"target_chat": "-404"})).status_code == 400
            assert (await client.put("/api/publishing/target", json={"target_chat": "-100200"})).status_code == 200
            await client.put("/api/publishing/account", json={"sender_account": None})
            assert store.default_target(7) == "-100200"
            assert store.publishing_preference(7)["sender_account"] is None
    asyncio.run(check())


@pytest.mark.parametrize("mode", ["default", "expired", "offline", "mismatch", "no_target", "failure"])
def test_default_account_routes_submission_without_silent_switch(tmp_path, monkeypatch, mode):
    store = Store(str(tmp_path / "bot.sqlite3"))
    store.add_target("-100200", "Archive")
    if mode != "no_target":
        store.set_default_target(7, "-100200")
    store.set_publishing_account(7, "user" if mode in {"offline", "mismatch"} else "bot")
    expiry = store.publishing_preference(7)["expires_at"]
    if mode == "expired":
        monkeypatch.setattr("forwarder.store.time.time", lambda: expiry)
    if mode == "mismatch":
        store.set_user_session_account(8)
    bot = SimpleNamespace(send_message=AsyncMock(), copy_message=AsyncMock(return_value=object()))
    if mode == "failure":
        from telegram.error import Forbidden
        bot.copy_message.side_effect = Forbidden("no access")
    app = SimpleNamespace(bot=bot, bot_data={"store": store, "user_session_enabled": True})
    asyncio.run(_offer_batch(app, 7, 7, [123], False))
    if mode == "default":
        bot.copy_message.assert_awaited_once()
        assert not app.bot_data["pending_copies"]
    else:
        assert app.bot_data["pending_copies"]
        markup = bot.send_message.call_args.kwargs["reply_markup"]
        prefix = "send:" if mode in {"no_target", "failure"} else "account:"
        assert markup.inline_keyboard[0][0].callback_data.startswith(prefix)
        if mode != "failure":
            bot.copy_message.assert_not_awaited()
    assert store.publishing_preference(7)["expires_at"] == expiry


def test_bot_selection_persists_and_start_reuses_default(tmp_path):
    store = Store(str(tmp_path / "menu.sqlite3"))
    message = SimpleNamespace(reply_text=AsyncMock())
    user = SimpleNamespace(id=7)
    query = SimpleNamespace(data="menu:publisher_set:bot", answer=AsyncMock(), message=message)
    update = SimpleNamespace(effective_user=user, effective_message=message, callback_query=query)
    context = SimpleNamespace(application=SimpleNamespace(bot_data={"store": store, "admin_ids": {7}}), user_data={})
    asyncio.run(menu_callback(update, context))
    assert store.publishing_preference(7)["sender_account"] == "bot"
    callbacks = {b.callback_data for row in message.reply_text.call_args.kwargs["reply_markup"].inline_keyboard for b in row}
    assert "menu:new_bot" in callbacks
    assert "menu:publisher_set:bot" not in callbacks
    query.data = "menu:publisher_clear"
    asyncio.run(menu_callback(update, context))
    assert store.publishing_preference(7)["sender_account"] is None
