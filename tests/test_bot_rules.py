import asyncio
from types import SimpleNamespace

import pytest
from telegram import Bot, Message
from telegram.error import BadRequest

from forwarder.bot import _caption_options, _matches_album, _matches_rule, _offer_batch, _relay_messages, _send_one, copy_callback, receive_forward
from forwarder.management import handle_flow_text
from forwarder.store import Store


def photo_message(message_id: int, caption: str = "A release photo") -> Message:
    bot = Bot("123456:ABCDEF")
    return Message.de_json(
        {
            "message_id": message_id,
            "date": 1_710_000_000,
            "chat": {"id": -1001234567890, "type": "channel", "title": "Source"},
            "media_group_id": "album-1",
            "caption": caption,
            "caption_entities": [{"type": "bold", "offset": 2, "length": 7}],
            "photo": [{"file_id": f"file-{message_id}", "file_unique_id": f"unique-{message_id}", "width": 20, "height": 20}],
        },
        bot,
    )


def test_rule_matches_media_and_any_configured_keyword():
    message = photo_message(1)
    rule = {"media_types": ["photo"], "filter_keywords": ["release", "weekly"]}
    assert _matches_rule(message, rule)
    assert not _matches_rule(message, {**rule, "media_types": ["video"]})
    assert not _matches_rule(message, {**rule, "filter_keywords": ["invoice"]})
    assert _matches_rule(message, {"media_types": ["all"], "filter_keywords": []})


def test_caption_prefix_keeps_entities_and_uses_utf16_offsets():
    message = photo_message(1)
    options = _caption_options(message, {"caption_mode": "prepend", "caption_text": "🔥"})
    assert options["caption"] == "🔥\n\nA release photo"
    assert options["caption_entities"][0].offset == 6

    appended = _caption_options(message, {"caption_mode": "append", "caption_text": "Archive"})
    assert appended["caption"] == "A release photo\n\nArchive"
    assert appended["caption_entities"][0].offset == 2


def test_album_scope_matches_combined_captions_and_excludes_the_whole_album():
    messages = [photo_message(1, "release photo"), photo_message(2, "weekly archive")]
    rule = {
        "media_types": ["photo"],
        "filter_keywords": ["release"],
        "exclude_keywords": ["spoiler"],
    }
    assert _matches_album(messages, rule)
    assert not _matches_album([*messages, photo_message(3, "spoiler")], rule)


class FakeBot:
    def __init__(self):
        self.copies = []
        self.albums = []
        self.sent = []
        self.username = "forwarder_test_bot"

    async def copy_message(self, **kwargs):
        self.copies.append(kwargs)
        return SimpleNamespace(message_id=100 + len(self.copies))

    async def copy_messages(self, **kwargs):
        self.albums.append(kwargs)
        return tuple(SimpleNamespace(message_id=100 + index) for index, _ in enumerate(kwargs["message_ids"]))

    async def send_message(self, *args, **kwargs):
        if args:
            kwargs["chat_id"] = args[0]
        if len(args) > 1:
            kwargs["text"] = args[1]
        self.sent.append(kwargs)
        return SimpleNamespace(message_id=100 + len(self.sent))


@pytest.mark.parametrize("album", [False, True])
@pytest.mark.parametrize("default_target", [False, True])
def test_forwarded_submission_exits_source_wizard_and_delivers(tmp_path, album, default_target):
    store = Store(str(tmp_path / "submission.sqlite3"))
    store.add_target("-100200", "Archive")
    if default_target:
        store.set_default_target(7, "-100200")
    bot = FakeBot()
    app = SimpleNamespace(bot_data={"store": store, "admin_ids": {7}, "user_session_enabled": False}, bot=bot)
    context = SimpleNamespace(
        application=app,
        user_data={"managed_flow": {"kind": "rule", "step": "source_chat", "settings": {"source_mode": "bot"}}},
    )

    async def submit():
        tasks = []
        app.create_task = lambda coro: tasks.append(asyncio.create_task(coro))
        for message_id in ([1, 2] if album else [1]):
            data = {
                "message_id": message_id, "date": 1710000000,
                "chat": {"id": 7, "type": "private", "first_name": "Owner"},
                "forward_origin": {"type": "hidden_user", "sender_user_name": "Source", "date": 1710000000},
                "text": "Original text, not a source ID",
            }
            if album:
                data.pop("text")
                data.update(media_group_id="submission-album", photo=[{
                    "file_id": f"photo-{message_id}", "file_unique_id": f"unique-{message_id}", "width": 20, "height": 20,
                }])
            update = SimpleNamespace(effective_message=Message.de_json(data, None), effective_user=SimpleNamespace(id=7))
            # Match production ordering: management runs before the receiver.
            await handle_flow_text(update, context)
            await receive_forward(update, context)
        await asyncio.gather(*tasks)

    asyncio.run(submit())
    assert "managed_flow" not in context.user_data
    assert len(bot.sent) == 1
    assert not bot.copies and not bot.albums
    assert bot.sent[0]["reply_markup"].inline_keyboard[0][0].callback_data.startswith("account:")
    pending = next(iter(app.bot_data["pending_copies"].values()))
    assert pending["message_ids"] == ([1, 2] if album else [1])


def test_relay_applies_filters_and_merges_matching_album(tmp_path):
    store = Store(str(tmp_path / "relay.sqlite3"))
    store.add_target("-100200", "Archive")
    store.add_rule(
        1,
        "-1001234567890",
        "-100200",
        {
            "media_types": ["photo"],
            "filter_keywords": ["release"],
            "album_mode": "merge",
        },
    )
    bot = FakeBot()
    app = SimpleNamespace(bot_data={"store": store}, bot=bot)

    asyncio.run(_relay_messages(app, [photo_message(1), photo_message(2)]))

    assert len(bot.albums) == 1
    assert bot.albums[0]["message_ids"] == [1, 2]
    assert bot.copies == []
    assert store.activity_summary()["success"] == 2


def test_default_target_auto_sends_without_showing_target_picker(tmp_path):
    store = Store(str(tmp_path / "default-target.sqlite3"))
    store.add_target("-100200", "Archive")
    store.set_default_target(7, "-100200")
    bot = FakeBot()
    app = SimpleNamespace(bot_data={"store": store}, bot=bot)

    asyncio.run(_offer_batch(app, 7, 123, [1], False))

    assert len(bot.sent) == 1
    assert "请选择发布账号" in bot.sent[0]["text"]
    assert bot.sent[0]["reply_markup"].inline_keyboard[0][0].callback_data.endswith(":bot")
    assert not bot.copies


def test_default_target_failure_falls_back_to_manual_target_picker(tmp_path):
    class FailingBot(FakeBot):
        async def copy_message(self, **kwargs):
            self.copies.append(kwargs)
            raise BadRequest("Bot cannot post to this target")

        async def send_message(self, *args, **kwargs):
            if args:
                kwargs["chat_id"] = args[0]
            if len(args) > 1:
                kwargs["text"] = args[1]
            return await super().send_message(**kwargs)

    store = Store(str(tmp_path / "default-target-fallback.sqlite3"))
    store.add_target("-100200", "Archive")
    store.set_default_target(7, "-100200")
    bot = FailingBot()
    app = SimpleNamespace(
        bot_data={
            "store": store,
            "pending_copies": {
                "pending-fail": {"owner_id": 7, "source_chat": 123, "message_ids": [1], "sent": set()}
            },
        },
        bot=bot,
    )

    class Query:
        data = "account:pending-fail:bot"
        from_user = SimpleNamespace(id=7)

        async def answer(self, *args, **kwargs):
            pass

        async def edit_message_text(self, text):
            self.text = text

    query = Query()
    update = SimpleNamespace(callback_query=query)
    context = SimpleNamespace(application=app, bot=bot)
    asyncio.run(copy_callback(update, context))

    assert len(bot.sent) == 1
    assert "请选择一个或多个目标" in bot.sent[0]["text"]
    assert "默认目标无法发送" in query.text
    assert bot.sent[0]["reply_markup"].inline_keyboard[0][0].callback_data.startswith("send:")
    assert store.activity_summary()["failed"] == 1


def test_selecting_bot_account_uses_default_target_immediately(tmp_path):
    class Query:
        data = "account:pending-1:bot"
        from_user = SimpleNamespace(id=7)

        async def answer(self, *args, **kwargs):
            pass

        async def edit_message_text(self, text):
            self.text = text

    store = Store(str(tmp_path / "account-bot.sqlite3"))
    store.add_target("-100200", "Archive")
    store.set_default_target(7, "-100200")
    bot = FakeBot()
    app = SimpleNamespace(
        bot_data={
            "store": store,
            "pending_copies": {
                "pending-1": {"owner_id": 7, "source_chat": 7, "message_ids": [12], "sent": set()}
            },
        },
        bot=bot,
    )
    query = Query()
    update = SimpleNamespace(callback_query=query)
    context = SimpleNamespace(application=app, bot=bot)

    asyncio.run(copy_callback(update, context))

    assert bot.copies == [{"chat_id": "-100200", "from_chat_id": 7, "message_id": 12}]
    assert "机器人账号" in query.text
    assert store.activity_summary()["success"] == 1


def test_personal_account_selection_queues_direct_task_for_matching_session(tmp_path):
    class Query:
        data = "account:pending-2:user"
        from_user = SimpleNamespace(id=7)

        async def answer(self, *args, **kwargs):
            self.answer_text = args[0] if args else ""

        async def edit_message_text(self, text):
            self.text = text

    store = Store(str(tmp_path / "account-user.sqlite3"))
    store.add_target("-100200", "Archive")
    store.set_default_target(7, "-100200")
    store.set_user_session_account(7)
    bot = FakeBot()
    app = SimpleNamespace(
        bot_data={
            "store": store,
            "user_session_enabled": True,
            "pending_copies": {
                "pending-2": {"owner_id": 7, "source_chat": 7, "message_ids": [31, 32], "sent": set()}
            },
        },
        bot=bot,
    )
    query = Query()
    update = SimpleNamespace(callback_query=query)
    context = SimpleNamespace(application=app, bot=bot)

    asyncio.run(copy_callback(update, context))

    assert not bot.copies and not bot.albums
    assert "已排队" in query.text
    task = store.user_tasks(7)[0]
    assert task["kind"] == "direct"
    assert task["source_message_ids"] == [31, 32]
    assert task["target_chat"] == "-100200"


def test_personal_account_selection_rejects_another_or_offline_session(tmp_path):
    class Query:
        data = "account:pending-3:user"
        from_user = SimpleNamespace(id=7)
        answer_text = ""

        async def answer(self, *args, **kwargs):
            self.answer_text = args[0] if args else ""

    store = Store(str(tmp_path / "account-mismatch.sqlite3"))
    store.add_target("-100200", "Archive")
    store.set_user_session_account(8)
    bot = FakeBot()
    app = SimpleNamespace(
        bot_data={
            "store": store,
            "user_session_enabled": True,
            "pending_copies": {
                "pending-3": {"owner_id": 7, "source_chat": 7, "message_ids": [45], "sent": set()}
            },
        },
        bot=bot,
    )
    query = Query()
    update = SimpleNamespace(callback_query=query)
    context = SimpleNamespace(application=app, bot=bot)

    asyncio.run(copy_callback(update, context))

    assert "不一致" in query.answer_text
    assert not store.user_tasks()


def test_album_scope_relays_the_complete_group_when_a_caption_matches(tmp_path):
    store = Store(str(tmp_path / "album-scope.sqlite3"))
    store.add_target("-100200", "Archive")
    store.add_rule(
        1,
        "-1001234567890",
        "-100200",
        {
            "media_types": ["photo"],
            "filter_keywords": ["release"],
            "filter_scope": "album",
            "album_mode": "merge",
        },
    )
    bot = FakeBot()
    app = SimpleNamespace(bot_data={"store": store}, bot=bot)

    asyncio.run(_relay_messages(app, [photo_message(1, "release photo"), photo_message(2, "weekly archive")]))

    assert bot.albums[0]["message_ids"] == [1, 2]
    assert store.activity_summary()["success"] == 2


def test_text_transform_that_removes_all_content_does_not_send_placeholder():
    bot = Bot("123456:ABCDEF")
    message = Message.de_json(
        {
            "message_id": 1,
            "date": 1_710_000_000,
            "chat": {"id": -1001234567890, "type": "channel", "title": "Source"},
            "text": "remove me",
        },
        bot,
    )
    fake_bot = FakeBot()
    app = SimpleNamespace(bot=fake_bot)

    delivered = asyncio.run(_send_one(app, "-100200", message, {"remove_keywords": ["remove me"]}))

    assert delivered is False
    assert fake_bot.sent == []
