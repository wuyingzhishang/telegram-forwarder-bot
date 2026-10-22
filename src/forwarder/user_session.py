from __future__ import annotations

import asyncio
import html as html_module
import logging
import os
from io import BytesIO
from pathlib import Path

from dotenv import load_dotenv
from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
)
from telegram.error import RetryAfter, TelegramError
from telethon import TelegramClient, events
from telethon.extensions import html as telethon_html
from telethon.errors import FloodWaitError
from telethon.tl.types import (
    DocumentAttributeAudio,
    DocumentAttributeSticker,
    DocumentAttributeVideo,
    PeerChannel,
    PeerChat,
)
from telethon.utils import get_peer_id

from .rule_text import transform_text
from .store import Store

logger = logging.getLogger("forwarder.user_session")


def _media_type(message) -> str:
    if getattr(message, "photo", None):
        return "photo"
    document = getattr(message, "document", None)
    if document is None:
        return "text" if getattr(message, "message", None) is not None else "unsupported"
    attributes = document.attributes
    if any(isinstance(item, DocumentAttributeSticker) for item in attributes):
        return "sticker"
    video = next((item for item in attributes if isinstance(item, DocumentAttributeVideo)), None)
    if video and video.round_message:
        return "video_note"
    audio = next((item for item in attributes if isinstance(item, DocumentAttributeAudio)), None)
    if audio and audio.voice:
        return "voice"
    if audio:
        return "audio"
    if video or str(document.mime_type or "").startswith("video/"):
        return "video"
    if str(document.mime_type or "").startswith("image/gif"):
        return "animation"
    return "document"


def _text_for(message, rule: dict[str, object], media: bool) -> str:
    original = str(getattr(message, "message", "") or "")
    value = original
    mode = "keep"
    prefix_shift = 0
    if media:
        mode = str(rule.get("caption_mode", "keep"))
        caption_text = str(rule.get("caption_text", ""))
        if mode == "remove":
            value = ""
        elif mode == "replace":
            value = caption_text
        elif mode == "prepend":
            prefix = caption_text + ("\n\n" if value else "")
            prefix_shift = len(prefix.encode("utf-16-le")) // 2
            value = prefix + value
        elif mode == "append":
            value = value + ("\n\n" if value else "") + caption_text
    link_entities = []
    if rule.get("remove_links") and mode not in {"remove", "replace"}:
        types = {
            "MessageEntityUrl": "url",
            "MessageEntityTextUrl": "text_link",
            "MessageEntityMention": "mention",
            "MessageEntityMentionName": "text_mention",
            "MessageEntityEmail": "email",
        }
        for entity in getattr(message, "entities", None) or []:
            entity_type = types.get(type(entity).__name__)
            if entity_type:
                item = {"type": entity_type, "offset": entity.offset + prefix_shift, "length": entity.length}
                if entity_type == "text_link":
                    item["url"] = entity.url
                link_entities.append(item)
    transformed, _ = transform_text(value, link_entities, rule)
    has_edits = any((
        rule.get("remove_links"), rule.get("remove_keywords"), rule.get("replacements"),
        rule.get("prefix_text"), rule.get("suffix_text"), media and mode != "keep",
    ))
    if not has_edits:
        return telethon_html.unparse(original, getattr(message, "entities", None) or [])
    return html_module.escape(transformed)


def _matches(message, rule: dict[str, object]) -> bool:
    types = rule.get("media_types", ["all"])
    message_type = _media_type(message)
    if "all" not in types and message_type not in types:
        return False
    searchable = str(getattr(message, "message", "") or "").casefold()
    include = rule.get("filter_keywords", [])
    exclude = rule.get("exclude_keywords", [])
    if include and not any(str(term).casefold() in searchable for term in include):
        return False
    return not any(str(term).casefold() in searchable for term in exclude)


def _matches_album(messages: list, rule: dict[str, object]) -> bool:
    types = rule.get("media_types", ["all"])
    album_types = {_media_type(message) for message in messages}
    if "all" not in types and not album_types.intersection(types):
        return False
    searchable = "\n".join(str(getattr(message, "message", "") or "") for message in messages).casefold()
    include = rule.get("filter_keywords", [])
    exclude = rule.get("exclude_keywords", [])
    if include and not any(str(term).casefold() in searchable for term in include):
        return False
    return not any(str(term).casefold() in searchable for term in exclude)


def _button(rule: dict[str, object]) -> InlineKeyboardMarkup | None:
    text, url = str(rule.get("button_text", "")), str(rule.get("button_url", ""))
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, url=url)]]) if text and url else None


def _source_matches(rule: dict[str, object], chat_id: int, chat) -> bool:
    source = str(rule["source_chat"]).casefold()
    if source == str(chat_id):
        return True
    if source == str(getattr(chat, "id", "")):
        return True
    username = getattr(chat, "username", None)
    return bool(username and source == f"@{username}".casefold())


async def _download_stream(client: TelegramClient, message, fallback_name: str = "telegram-media.bin") -> BytesIO | None:
    stream = BytesIO()
    downloaded = await client.download_media(message, file=stream)
    if downloaded is None:
        return None
    stream.seek(0)
    name = getattr(getattr(message, "file", None), "name", None) or fallback_name
    stream.name = name
    return stream


async def _download(client: TelegramClient, message, fallback_name: str = "telegram-media.bin") -> InputFile | None:
    stream = await _download_stream(client, message, fallback_name)
    return InputFile(stream, filename=stream.name) if stream is not None else None


async def _send_one(client: TelegramClient, bot: Bot, target: str, message, rule: dict[str, object]) -> bool:
    if rule.get("sender_mode") == "forward":
        await client.forward_messages(target, message)
        return True
    kind = _media_type(message)
    text = _text_for(message, rule, kind != "text")
    user_sender = rule.get("sender_account") == "user"
    button = _button(rule)
    if kind == "text":
        if not text.strip():
            return False
        if user_sender:
            await client.send_message(target, text, parse_mode="html")
        else:
            await bot.send_message(chat_id=target, text=text, parse_mode="HTML", reply_markup=button)
        return True
    fallback_names = {
        "photo": "telegram-photo.jpg", "video": "telegram-video.mp4", "audio": "telegram-audio.mp3",
        "voice": "telegram-voice.ogg", "animation": "telegram-animation.mp4", "sticker": "telegram-sticker.webp",
        "video_note": "telegram-video-note.mp4",
    }
    if user_sender:
        media = await _download_stream(client, message, fallback_names.get(kind, "telegram-document.bin"))
        if media is None:
            return False
        await client.send_file(
            target,
            media,
            caption=text or None,
            parse_mode="html",
            force_document=kind == "document",
            voice_note=kind == "voice",
            video_note=kind == "video_note",
        )
        return True
    media = await _download(client, message, fallback_names.get(kind, "telegram-document.bin"))
    if media is None:
        return False
    caption = text or None
    kwargs = {"chat_id": target, "caption": caption, "parse_mode": "HTML", "reply_markup": button}
    if kind == "photo":
        await bot.send_photo(photo=media, **kwargs)
    elif kind == "video":
        await bot.send_video(video=media, **kwargs)
    elif kind == "audio":
        await bot.send_audio(audio=media, **kwargs)
    elif kind == "voice":
        await bot.send_voice(voice=media, **kwargs)
    elif kind == "animation":
        await bot.send_animation(animation=media, **kwargs)
    elif kind == "sticker":
        await bot.send_sticker(sticker=media, reply_markup=button, chat_id=target)
    elif kind == "video_note":
        await bot.send_video_note(video_note=media, chat_id=target)
    else:
        await bot.send_document(document=media, **kwargs)
    return True


async def _send_album(client: TelegramClient, bot: Bot, target: str, messages: list, rule: dict[str, object]) -> int:
    if rule.get("sender_account") == "user":
        files = []
        captions = []
        for message in messages:
            kind = _media_type(message)
            if kind not in {"photo", "video", "document", "audio"}:
                continue
            fallback = {
                "photo": "telegram-photo.jpg", "video": "telegram-video.mp4",
                "audio": "telegram-audio.mp3", "document": "telegram-document.bin",
            }[kind]
            stream = await _download_stream(client, message, fallback)
            if stream is None:
                continue
            files.append(stream)
            captions.append(_text_for(message, rule, True) or "")
        if not files:
            return 0
        sent = await client.send_file(target, files, caption=captions, parse_mode="html")
        return len(sent) if isinstance(sent, list) else int(sent is not None)
    media_items = []
    included_messages = []
    for message in messages:
        kind = _media_type(message)
        if kind not in {"photo", "video", "document", "audio"}:
            continue
        fallback = {
            "photo": "telegram-photo.jpg", "video": "telegram-video.mp4",
            "audio": "telegram-audio.mp3", "document": "telegram-document.bin",
        }[kind]
        file = await _download(client, message, fallback)
        if file is None:
            continue
        caption = _text_for(message, rule, True) or None
        options = {"media": file, "caption": caption, "parse_mode": "HTML"}
        constructor = {
            "photo": InputMediaPhoto,
            "video": InputMediaVideo,
            "document": InputMediaDocument,
            "audio": InputMediaAudio,
        }[kind]
        media_items.append(constructor(**options))
        included_messages.append(message)
    if not media_items:
        return 0
    if len(media_items) == 1:
        return int(await _send_one(client, bot, target, included_messages[0], rule))
    delivered = await bot.send_media_group(chat_id=target, media=media_items)
    button = _button(rule)
    if button and delivered:
        await bot.edit_message_reply_markup(
            chat_id=target,
            message_id=delivered[-1].message_id,
            reply_markup=button,
        )
    return len(delivered)


async def _relay(client: TelegramClient, bot: Bot, store: Store, messages: list, chat) -> None:
    if not messages:
        return
    chat_id = get_peer_id(chat)
    if getattr(chat, "noforwards", False) or any(getattr(message, "noforwards", False) for message in messages):
        logger.info("Skipped protected content from source %s", chat_id)
        return
    rules = [
        rule for rule in store.managed_rules()
        if rule["enabled"] and rule["source_mode"] == "user" and _source_matches(rule, chat_id, chat)
    ]
    for rule in rules:
        if rule.get("filter_scope") == "album" and len(messages) > 1:
            selected = messages if _matches_album(messages, rule) else []
        else:
            selected = [message for message in messages if _matches(message, rule)]
        if not selected:
            continue
        target = str(rule["target_chat"])
        try:
            while True:
                try:
                    if rule.get("album_mode") == "merge" and len(selected) > 1 and rule.get("sender_mode") == "forward":
                        forwarded = await client.forward_messages(target, selected)
                        delivered = len(forwarded) if isinstance(forwarded, list) else int(forwarded is not None)
                    elif rule.get("album_mode") == "merge" and len(selected) > 1:
                        delivered = await _send_album(client, bot, target, selected, rule)
                    else:
                        delivered = 0
                        for message in selected:
                            delivered += int(await _send_one(client, bot, target, message, rule))
                    break
                except FloodWaitError as exc:
                    await asyncio.sleep(float(exc.seconds))
                except RetryAfter as exc:
                    await asyncio.sleep(float(exc.retry_after))
            if delivered:
                store.record_activity(target, "success", delivered)
        except TelegramError:
            store.record_activity(target, "failed", len(selected))
            logger.exception("Telegram API delivery failed for user-session rule %s", rule["id"])
        except Exception:
            store.record_activity(target, "failed", len(selected))
            logger.exception("User-session relay failed for rule %s", rule["id"])


async def _resolve_source(client: TelegramClient, source: str):
    if source.startswith("@"):
        return await client.get_entity(source)
    if source.startswith("-100") and source[4:].isdigit():
        return await client.get_entity(PeerChannel(int(source[4:])))
    if source.startswith("-") and source[1:].isdigit():
        return await client.get_entity(PeerChat(int(source[1:])))
    if source.isdigit():
        return await client.get_entity(int(source))
    raise ValueError("Source must be a numeric Telegram chat ID or public @username.")


def _task_batches(messages: list) -> list[list]:
    batches: list[list] = []
    for message in sorted(messages, key=lambda item: item.id):
        grouped_id = getattr(message, "grouped_id", None)
        if grouped_id is not None and batches and getattr(batches[-1][-1], "grouped_id", None) == grouped_id:
            batches[-1].append(message)
        else:
            batches.append([message])
    return batches


async def _run_user_task(client: TelegramClient, bot: Bot, store: Store, task: dict[str, object]) -> None:
    task_id = int(task["id"])
    if task["kind"] == "direct":
        account = await client.get_me()
        if account is None or int(account.id) != int(task["owner_id"]):
            raise ValueError(
                "个人账号会话登录的 Telegram 账号与提交这条消息的账号不一致。"
                "请使用提交消息的同一账号登录，或选择机器人账号发送。"
            )
        entity = await _resolve_source(client, str(task["source_chat"]))
        requested_ids = [int(message_id) for message_id in task.get("source_message_ids", [])]
        fetched = await client.get_messages(entity, ids=requested_ids)
        if not isinstance(fetched, list):
            fetched = [fetched] if fetched is not None else []
        by_id = {int(message.id): message for message in fetched if message is not None}
        messages = [by_id[message_id] for message_id in requested_ids if message_id in by_id]
        if len(messages) != len(requested_ids):
            raise ValueError(
                "无法从机器人私聊中读取这条消息。请确认使用提交消息的同一账号完成登录，"
                "并重新转发一次。"
            )
        if any(getattr(message, "noforwards", False) for message in messages):
            raise ValueError("这条消息已启用 Telegram 内容保护，不能使用个人账号复制。")
        rule = {
            "sender_account": "user", "sender_mode": "copy", "caption_mode": "keep", "caption_text": "",
            "remove_keywords": [], "replacements": [], "prefix_text": "", "suffix_text": "",
            "remove_links": False, "button_text": "", "button_url": "",
        }
        rule.update(task.get("rule_snapshot") or {})
        rule["sender_account"] = "user"
        target = str(task["target_chat"])
        if rule.get("album_mode", "merge") == "merge" and len(messages) > 1 and getattr(messages[0], "grouped_id", None) is not None and all(
            getattr(message, "grouped_id", None) == messages[0].grouped_id for message in messages
        ):
            delivered = await _send_album(client, bot, target, messages, rule)
        else:
            delivered = 0
            for message in messages:
                delivered += int(await _send_one(client, bot, target, message, rule))
        store.update_user_task(task_id, progress=len(messages))
        if delivered:
            store.record_activity(target, "success", delivered)
        store.set_user_task_status(task_id, "completed")
        await bot.send_message(
            chat_id=int(task["owner_id"]),
            text=f"个人账号转发任务 #{task_id} 已完成，发送 {delivered} 条消息。",
        )
        return

    entity = await _resolve_source(client, str(task["source_chat"]))
    if getattr(entity, "noforwards", False):
        raise ValueError("This source has content protection enabled.")
    if task["kind"] == "extract":
        if int(task.get("cursor", 0)) > 0:
            store.set_user_task_status(task_id, "completed")
            return
        message_id = int(task["source_message_id"] or 0)
        message = await client.get_messages(entity, ids=message_id)
        if message is None:
            raise ValueError("Source message was not found or is not accessible to this account.")
        messages = [message]
        if getattr(message, "grouped_id", None) is not None:
            nearby = await client.get_messages(entity, ids=list(range(max(1, message_id - 10), message_id + 11)))
            messages = [
                item for item in nearby
                if item is not None and getattr(item, "grouped_id", None) == message.grouped_id
            ]
        batches = _task_batches(messages)
    else:
        snapshot_start = int(task.get("snapshot_start", 0))
        snapshot_end = int(task.get("snapshot_end", 0))
        if snapshot_start and snapshot_end:
            messages = await client.get_messages(
                entity,
                limit=int(task["item_limit"]),
                min_id=snapshot_start - 1,
                max_id=snapshot_end + 1,
            )
        else:
            messages = await client.get_messages(entity, limit=int(task["item_limit"]))
            if messages:
                snapshot_start = min(item.id for item in messages)
                snapshot_end = max(item.id for item in messages)
                store.update_user_task(task_id, snapshot_start=snapshot_start, snapshot_end=snapshot_end)
        remaining = [item for item in messages if item.id > int(task.get("cursor", 0))]
        batches = _task_batches(list(remaining))
    rule = {
        "sender_mode": "copy", "caption_mode": "keep", "caption_text": "",
        "remove_keywords": [], "replacements": [], "prefix_text": "", "suffix_text": "",
        "remove_links": False, "button_text": "", "button_url": "",
    }
    target = str(task["target_chat"])
    processed = int(task.get("progress", 0))
    for batch in batches:
        current = store.user_task(task_id)
        if current is None or current["status"] in {"paused", "cancelled"}:
            return
        if any(getattr(item, "noforwards", False) for item in batch):
            raise ValueError("This source has content protection enabled.")
        while True:
            try:
                if len(batch) > 1:
                    delivered = await _send_album(client, bot, target, batch, rule)
                else:
                    delivered = int(await _send_one(client, bot, target, batch[0], rule))
                break
            except FloodWaitError as exc:
                delay = float(exc.seconds)
            except RetryAfter as exc:
                delay = float(exc.retry_after)
            await asyncio.sleep(delay)
            current = store.user_task(task_id)
            if current is None or current["status"] in {"paused", "cancelled"}:
                return
        processed += len(batch)
        if delivered:
            store.record_activity(target, "success", delivered)
        store.update_user_task(task_id, cursor=min(item.id for item in batch), progress=processed)
    current = store.user_task(task_id)
    if current and current["status"] == "running":
        store.set_user_task_status(task_id, "completed")


async def _task_loop(client: TelegramClient, bot: Bot, store: Store) -> None:
    while True:
        store.heartbeat_user_session()
        task = store.claim_user_task()
        if task is None:
            await asyncio.sleep(2)
            continue
        try:
            await _run_user_task(client, bot, store, task)
        except FloodWaitError as exc:
            store.set_user_task_status(int(task["id"]), "paused", f"Telegram requested a wait of {exc.seconds}s")
        except RetryAfter as exc:
            store.set_user_task_status(int(task["id"]), "paused", f"Bot API rate limit; retry after {exc.retry_after}s")
        except Exception as exc:
            logger.exception("User-session task %s failed", task["id"])
            store.set_user_task_status(int(task["id"]), "failed", f"{type(exc).__name__}: {exc}")
            if task["kind"] == "direct":
                store.record_activity(str(task["target_chat"]), "failed", int(task["item_limit"]))
                try:
                    await bot.send_message(
                        chat_id=int(task["owner_id"]),
                        text=f"个人账号转发任务 #{task['id']} 失败：{exc}",
                    )
                except Exception:
                    logger.exception("Could not notify owner about failed direct task %s", task["id"])


async def _heartbeat_loop(store: Store) -> None:
    while True:
        store.heartbeat_user_session()
        await asyncio.sleep(10)


async def run() -> None:
    load_dotenv()
    api_id = os.getenv("API_ID", "").strip()
    api_hash = os.getenv("API_HASH", "").strip()
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    session_path = Path(os.getenv("USER_SESSION_PATH", "data/user-session/forwarder"))
    if not api_id.isdigit() or not api_hash or not bot_token:
        raise RuntimeError("Set API_ID, API_HASH, and BOT_TOKEN before starting user-session mode.")
    session_path.parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(session_path), int(api_id), api_hash)
    bot = Bot(bot_token)
    store = Store(os.getenv("DATABASE_PATH", "data/forwarder.sqlite3"))
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError("No authorized user session. Run python -m forwarder.user_session_login first.")
        await bot.initialize()
        account = await client.get_me()
        await client.get_dialogs(limit=None)
        store.set_user_session_account(int(account.id))
        logger.info("User-session worker connected for account id %s", account.id)

        @client.on(events.NewMessage)
        async def on_message(event):
            if event.message.grouped_id is not None:
                return
            chat = await event.get_chat()
            await _relay(client, bot, store, [event.message], chat)

        @client.on(events.Album)
        async def on_album(event):
            chat = await event.get_chat()
            await _relay(client, bot, store, list(event.messages), chat)

        task = asyncio.create_task(_task_loop(client, bot, store), name="user-session-task-worker")
        heartbeat_task = asyncio.create_task(_heartbeat_loop(store), name="user-session-heartbeat")
        await client.run_until_disconnected()
    finally:
        store.set_user_session_account(None)
        heartbeat_task = locals().get("heartbeat_task")
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
        task = locals().get("task")
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await client.disconnect()
        await bot.shutdown()


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(run())


if __name__ == "__main__":
    main()
