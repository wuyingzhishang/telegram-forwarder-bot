import asyncio
from io import StringIO
from types import SimpleNamespace

from telethon.errors import SessionPasswordNeededError

from forwarder.rule_text import transform_text
from forwarder.user_session import _matches, _matches_album, _media_type, _run_user_task, _send_one, _source_matches, _text_for
from forwarder.store import Store
from forwarder.user_session_login import _login_with_qr, _print_qr


def text_message(value: str):
    return SimpleNamespace(message=value, photo=None, document=None)


def test_user_session_rule_filters_text_and_album_as_a_group():
    first = text_message("weekly release")
    second = text_message("archive copy")
    rule = {
        "media_types": ["text"],
        "filter_keywords": ["release"],
        "exclude_keywords": ["spoiler"],
    }

    assert _media_type(first) == "text"
    assert _matches(first, rule)
    assert not _matches(second, rule)
    assert _matches_album([first, second], rule)
    assert not _matches_album([first, text_message("spoiler")], rule)


def test_user_session_matches_public_source_names_and_transforms_text():
    chat = SimpleNamespace(id=145, username="DailyNews")
    rule = {"source_chat": "@dailynews"}
    text_rule = {"remove_keywords": ["draft"], "prefix_text": "Saved"}

    assert _source_matches(rule, -1000000145, chat)
    assert _text_for(text_message("Draft: release"), text_rule, media=False) == "Saved\n\n: release"


def test_personal_sender_publishes_text_from_the_logged_in_user_account():
    class Client:
        def __init__(self):
            self.sent = []

        async def send_message(self, *args, **kwargs):
            self.sent.append((args, kwargs))

    class Bot:
        async def send_message(self, **kwargs):
            raise AssertionError("Bot API must not publish a personal-account rule")

    client = Client()
    rule = {"sender_account": "user", "sender_mode": "copy"}
    assert asyncio.run(_send_one(client, Bot(), "-100target", text_message("hello"), rule))
    assert client.sent == [(("-100target", "hello"), {"parse_mode": "html"})]


def test_direct_personal_task_reads_bot_private_message_and_sends_as_matching_account(tmp_path):
    class Client:
        def __init__(self):
            self.sent = []
            self.messages = [SimpleNamespace(id=51, message="hello from forwarded message", photo=None, document=None)]

        async def get_me(self):
            return SimpleNamespace(id=7)

        async def get_entity(self, source):
            assert source == "@forwarder_bot"
            return source

        async def get_messages(self, entity, ids):
            assert entity == "@forwarder_bot"
            assert ids == [51]
            return self.messages

        async def send_message(self, *args, **kwargs):
            self.sent.append((args, kwargs))

    class Bot:
        def __init__(self):
            self.sent = []

        async def send_message(self, **kwargs):
            self.sent.append(kwargs)

    store = Store(str(tmp_path / "direct-personal.sqlite3"))
    store.add_target("-100target", "Archive")
    task_id = store.create_user_task(
        7, "direct", "@forwarder_bot", "-100target", source_message_ids=[51]
    )
    task = store.claim_user_task()
    client, bot = Client(), Bot()

    asyncio.run(_run_user_task(client, bot, store, task))

    assert client.sent == [(("-100target", "hello from forwarded message"), {"parse_mode": "html"})]
    assert store.user_task(task_id)["status"] == "completed"
    assert store.activity_summary()["success"] == 1
    assert "已完成" in bot.sent[0]["text"]


def test_direct_personal_task_refuses_when_session_account_does_not_match(tmp_path):
    class Client:
        async def get_me(self):
            return SimpleNamespace(id=8)

    store = Store(str(tmp_path / "direct-mismatch.sqlite3"))
    task_id = store.create_user_task(
        7, "direct", "@forwarder_bot", "-100target", source_message_ids=[51]
    )
    task = store.claim_user_task()

    try:
        asyncio.run(_run_user_task(Client(), SimpleNamespace(), store, task))
    except ValueError as exc:
        assert "不一致" in str(exc)
    else:
        raise AssertionError("A different personal account must not deliver this task")

    assert store.user_task(task_id)["status"] == "running"


def test_photo_messages_are_detected_for_photo_only_rules():
    photo = SimpleNamespace(message="caption", photo=object(), document=None)
    assert _media_type(photo) == "photo"
    assert _matches(photo, {"media_types": ["photo"]})
    assert not _matches(photo, {"media_types": ["video"]})


def test_remove_links_works_without_message_entities():
    text, _ = transform_text("open https://example.com/path, thanks", [], {"remove_links": True})
    assert text == "open, thanks"


def test_user_session_removes_hidden_url_entities():
    class MessageEntityTextUrl:
        offset = 3
        length = 5
        url = "https://example.com"

    message = SimpleNamespace(
        message="go there now",
        photo=None,
        document=None,
        entities=[MessageEntityTextUrl()],
    )
    assert _text_for(message, {"remove_links": True}, media=False) == "go now"


def test_qr_login_refreshes_expired_qr_and_waits_for_mobile_confirmation(monkeypatch):
    class FakeQrLogin:
        url = "tg://login?token=temporary"

        def __init__(self):
            self.wait_calls = 0
            self.refreshes = 0

        async def wait(self):
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise asyncio.TimeoutError
            return SimpleNamespace(id=42)

        async def recreate(self):
            self.refreshes += 1

    qr_login = FakeQrLogin()

    class FakeClient:
        async def qr_login(self):
            return qr_login

    rendered_urls = []
    monkeypatch.setattr("forwarder.user_session_login._print_qr", rendered_urls.append)

    account = asyncio.run(_login_with_qr(FakeClient()))

    assert account.id == 42
    assert qr_login.refreshes == 1
    assert rendered_urls == [qr_login.url, qr_login.url]


def test_qr_login_requests_two_step_password_when_telegram_requires_it(monkeypatch):
    class FakeQrLogin:
        url = "tg://login?token=temporary"

        async def wait(self):
            raise SessionPasswordNeededError(request=None)

    class FakeClient:
        def __init__(self):
            self.password = None

        async def qr_login(self):
            return FakeQrLogin()

        async def sign_in(self, password):
            self.password = password

        async def get_me(self):
            return SimpleNamespace(id=42)

    client = FakeClient()
    monkeypatch.setattr("forwarder.user_session_login._print_qr", lambda _url: None)
    monkeypatch.setattr("forwarder.user_session_login.getpass.getpass", lambda _prompt: "test-password")

    account = asyncio.run(_login_with_qr(client))

    assert account.id == 42
    assert client.password == "test-password"


def test_qr_renderer_outputs_a_compact_code_without_printing_the_login_url():
    import pytest

    pytest.importorskip("qrcode")
    url = "tg://login?token=unit-test"
    output = StringIO()

    _print_qr(url, output)

    lines = output.getvalue().splitlines()
    qr_lines = lines[1:-1]
    assert "token=" not in output.getvalue()
    assert all(len(line) == len(qr_lines[0]) for line in qr_lines)
    assert len(qr_lines[0]) <= 80
    assert any(char in "█▀▄" for line in qr_lines for char in line)
