import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from telegram import Bot, Message
from telegram.error import BadRequest

from forwarder.bot import _offer_batch, _deliver_submission
from forwarder.dashboard import create_app
from forwarder.management import menu_callback, handle_flow_text, wizard_callback, _visible_steps
from forwarder.store import Store


def message(mid=1, text="release old https://example.com", photo=False):
    data = {"message_id": mid, "date": 1, "chat": {"id": 7, "type": "private"}}
    if photo:
        data.update(photo=[{"file_id": f"p{mid}", "file_unique_id": f"u{mid}", "width": 20, "height": 20}], media_group_id="album")
        if text is not None:
            data["caption"] = text
    else:
        data["text"] = text
    return Message.de_json(data, Bot("123456:ABCDEF"))


def setup(tmp_path, **settings):
    store = Store(str(tmp_path / "rules.sqlite3"))
    store.add_target("-100200", "Archive")
    rid = store.save_manual_rule(7, {"name": "普通图片", "target_chat": "-100200", **settings})
    store.select_manual_rule(7, rid)
    bot = SimpleNamespace(username="test_bot", send_message=AsyncMock(), copy_message=AsyncMock(return_value=True), send_media_group=AsyncMock(return_value=[SimpleNamespace(message_id=101), SimpleNamespace(message_id=102)]), edit_message_reply_markup=AsyncMock())
    app = SimpleNamespace(bot=bot, bot_data={"store": store, "user_session_enabled": True, "admin_ids": {99}})
    return store, rid, app


def test_manual_rule_is_owner_scoped_and_not_an_auto_listener(tmp_path):
    store, rid, _ = setup(tmp_path)
    assert not store.managed_rules() and not store.active_rules(7)
    assert store.manual_rules(8) == []
    with pytest.raises(ValueError):
        store.select_manual_rule(8, rid)
    with pytest.raises(ValueError):
        store.save_manual_rule(8, {"target_chat": "-100200"}, rid)
    assert not store.delete_manual_rule(8, rid)
    assert store.target_rule_count("-100200") == 1
    store.delete_manual_rule(7, rid)
    assert store.selected_manual_rule_id(7) == rid
    assert store.target_rule_count("-100200") == 0
    store.select_manual_rule(7, None)
    assert store.selected_manual_rule_id(7) is None


def test_manual_text_filters_and_edits_before_delivery(tmp_path):
    store, _, app = setup(tmp_path, media_types=["text"], filter_keywords=["release"], remove_links=True,
                          replacements=[{"find": "old", "replace": "new"}], suffix_text="频道署名")
    asyncio.run(_offer_batch(app, 7, 7, [1], False, messages=[message()]))
    outgoing = app.bot.send_message.call_args_list[0].kwargs
    assert outgoing["chat_id"] == "-100200"
    assert outgoing["text"] == "release new\n\n频道署名"
    assert store.activity_summary()["success"] == 1
    app.bot.send_message.reset_mock()
    asyncio.run(_offer_batch(app, 7, 7, [2], False, messages=[message(2, "unmatched")]))
    assert "已跳过" in app.bot.send_message.call_args.args[1]
    assert store.activity_summary()["success"] == 1


@pytest.mark.parametrize("change", ["disabled", "deleted", "target_removed"])
def test_invalid_selected_rule_never_falls_back_to_default(tmp_path, change):
    store, rid, app = setup(tmp_path)
    store.set_publishing_account(7, "bot")
    store.set_default_target(7, "-100200")
    if change == "disabled":
        store.save_manual_rule(7, {**store.manual_rule(7, rid), "enabled": False}, rid)
    elif change == "deleted":
        store.delete_manual_rule(7, rid)
    else:
        store.remove_target("-100200")
    asyncio.run(_offer_batch(app, 7, 7, [1], False, messages=[message()]))
    app.bot.copy_message.assert_not_awaited()
    assert not store.user_tasks()


def test_album_rule_adds_caption_to_captionless_photo_and_applies_button(tmp_path):
    store, _, app = setup(tmp_path, album_mode="merge", filter_scope="album", filter_keywords=["release"],
                          suffix_text="署名", button_text="打开", button_url="https://example.com")
    asyncio.run(_offer_batch(app, 7, 7, [1, 2], True, messages=[message(1, "release", True), message(2, None, True)]))
    media = app.bot.send_media_group.call_args.kwargs["media"]
    assert media[1].caption == "署名"
    app.bot.edit_message_reply_markup.assert_awaited_once()
    assert store.activity_summary()["success"] == 2


def test_retry_sends_only_unsent_messages(tmp_path):
    store, _, app = setup(tmp_path)
    app.bot.copy_message.side_effect = [True, BadRequest("temporary"), True]
    asyncio.run(_offer_batch(app, 7, 7, [1, 2], True, messages=[message(1, "a", True), message(2, "b", True)]))
    item = next(iter(app.bot_data["pending_copies"].values()))
    assert item["delivered_ids"] == {1}
    result, error = asyncio.run(_deliver_submission(app, item, "-100200"))
    assert error is None and result == 1
    assert [call.kwargs["message_id"] for call in app.bot.copy_message.call_args_list] == [1, 2, 2]


def test_personal_queue_uses_rule_snapshot_and_never_switches_account(tmp_path):
    store, rid, app = setup(tmp_path, sender_account="user", suffix_text="original")
    asyncio.run(_offer_batch(app, 7, 7, [1], False, messages=[message()]))
    assert not store.user_tasks()
    store.set_user_session_account(7)
    asyncio.run(_offer_batch(app, 7, 7, [2], False, messages=[message(2)]))
    task = store.user_tasks(7)[0]
    assert task["rule_snapshot"]["suffix_text"] == "original"
    store.save_manual_rule(7, {**store.manual_rule(7, rid), "suffix_text": "changed"}, rid)
    assert store.user_task(task["id"])["rule_snapshot"]["suffix_text"] == "original"
    app.bot.copy_message.assert_not_awaited()


def test_personal_worker_applies_saved_processing_options(tmp_path):
    from forwarder.user_session import _run_user_task
    store, rid, app = setup(tmp_path, sender_account="user", remove_links=True, suffix_text="归档")
    store.set_user_session_account(7)
    asyncio.run(_offer_batch(app, 7, 7, [51], False, messages=[message(51)]))
    task = store.claim_user_task()
    client = SimpleNamespace(
        get_me=AsyncMock(return_value=SimpleNamespace(id=7)),
        get_entity=AsyncMock(return_value="@test_bot"),
        get_messages=AsyncMock(return_value=[SimpleNamespace(id=51, message="release old https://example.com", photo=None, document=None)]),
        send_message=AsyncMock(),
    )
    asyncio.run(_run_user_task(client, app.bot, store, task))
    assert client.send_message.call_args.args == ("-100200", "release old\n\n归档")
    assert store.user_task(task["id"])["status"] == "completed"


def test_non_admin_can_create_manual_rule_without_source_steps(tmp_path):
    store, _, app = setup(tmp_path)
    msg = SimpleNamespace(reply_text=AsyncMock(), chat_id=7, forward_origin=None, text="测试规则")
    query = SimpleNamespace(data="menu:manual_new", message=msg, answer=AsyncMock())
    update = SimpleNamespace(effective_user=SimpleNamespace(id=7), effective_message=msg, callback_query=query)
    ctx = SimpleNamespace(application=app, user_data={})
    asyncio.run(menu_callback(update, ctx))
    flow = ctx.user_data["managed_flow"]
    assert flow["step"] == "sender_account" and flow["kind"] == "manual"
    assert "source_chat" not in _visible_steps(flow) and "source_mode" not in _visible_steps(flow)
    query.data = "wiz:set:sender_account:bot"
    asyncio.run(menu_callback(update, ctx))
    assert flow["step"] == "target_chat"
    query.data = "wiz:target:-100200"
    asyncio.run(menu_callback(update, ctx))
    assert flow["step"] == "media_types"
    flow["step"] = "name"
    asyncio.run(handle_flow_text(update, ctx))
    assert flow["step"] == "review"
    query.data = "wiz:set:review:save"
    asyncio.run(menu_callback(update, ctx))
    assert len(store.manual_rules(7)) == 2
    assert not store.managed_rules()


def test_manual_api_crud_selection_auth_and_validation(tmp_path, monkeypatch):
    path = str(tmp_path / "api.sqlite3")
    for key, value in {"DATABASE_PATH": path, "ADMIN_IDS": "7", "DASHBOARD_USERNAME": "test", "DASHBOARD_PASSWORD": "test"}.items():
        monkeypatch.setenv(key, value)
    store = Store(path)
    store.add_target("-100200", "Archive")
    other = store.save_manual_rule(8, {"target_chat": "-100200"})
    async def check():
        async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
            assert (await client.get("/api/manual-rules")).status_code == 401
            client.auth = ("test", "test")
            payload = {"target_chat": "-100200", "name": "清理链接", "remove_links": True}
            assert (await client.post("/api/manual-rules", json=payload)).status_code == 403
            client.headers["X-Relay-Request"] = "dashboard"
            response = await client.post("/api/manual-rules", json=payload)
            assert response.status_code == 201
            rid = response.json()["id"]
            assert "source_chat" not in response.json()
            assert (await client.put("/api/manual-selection", json={"rule_id": other})).status_code == 400
            assert (await client.delete(f"/api/manual-rules/{other}")).status_code == 404
            assert (await client.put("/api/manual-selection", json={"rule_id": rid})).status_code == 200
            assert (await client.get("/api/manual-rules")).json()["selected_id"] == rid
            assert (await client.delete("/api/targets/-100200")).status_code == 409
            assert (await client.post("/api/manual-rules", json={**payload, "sender_mode": "forward"})).status_code == 422
            assert (await client.put(f"/api/manual-rules/{rid}", json={**payload, "enabled": False})).status_code == 200
            assert (await client.put("/api/manual-selection", json={"rule_id": rid})).status_code == 400
            assert (await client.delete(f"/api/manual-rules/{rid}")).status_code == 204
            assert (await client.get("/api/manual-rules")).json() == {"rules": [], "selected_id": rid}
            assert (await client.put("/api/manual-selection", json={"rule_id": None})).status_code == 200
    asyncio.run(check())
