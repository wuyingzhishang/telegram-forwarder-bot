from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
    MessageEntity,
    MenuButtonCommands,
    Update,
)
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .management import register_management_handlers
from .store import Store
from .rule_text import transform_text
from .links import parse_message_link

logger = logging.getLogger(__name__)
_pending_rules: dict[int, dict[str, str]] = {}
_albums: dict[tuple[int, str], dict[str, object]] = {}
_relay_albums: dict[tuple[int, str], dict[str, object]] = {}


def _authorized(user_id: int | None, context: ContextTypes.DEFAULT_TYPE) -> bool:
    return user_id is not None and user_id in context.application.bot_data["admin_ids"]


async def _require_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if not _authorized(user.id if user else None, context):
        if update.effective_message:
            await update.effective_message.reply_text("此机器人仅允许配置的管理员使用。请检查 .env 中的 ADMIN_IDS。")
        return False
    return True


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from .management import send_main_menu

    await send_main_menu(update, context)


async def set_source(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    if not context.args:
        await update.effective_message.reply_text("用法：/source @channel 或 /source -100123456789")
        return
    owner_id = update.effective_user.id
    try:
        chat = await context.bot.get_chat(context.args[0])
    except (BadRequest, Forbidden) as exc:
        await update.effective_message.reply_text(f"机器人无法访问这个来源，请确认聊天 ID/用户名和机器人权限。\n{exc}")
        return
    _pending_rules.setdefault(owner_id, {})["source"] = str(chat.id)
    await update.effective_message.reply_text(f"来源已设为 {chat.title or chat.username or chat.id} ({chat.id})。接下来发送 /target <聊天>。")


async def set_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    if not context.args:
        await update.effective_message.reply_text("用法：/target @channel 或 /target -100123456789")
        return
    owner_id = update.effective_user.id
    try:
        chat = await context.bot.get_chat(context.args[0])
    except (BadRequest, Forbidden) as exc:
        await update.effective_message.reply_text(f"机器人无法访问这个目标，请先将机器人加入目标聊天并检查权限。\n{exc}")
        return
    _pending_rules.setdefault(owner_id, {})["target"] = str(chat.id)
    await update.effective_message.reply_text(f"目标已设为 {chat.title or chat.username or chat.id} ({chat.id})。发送 /save 保存规则。")


async def save_rule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    owner_id = update.effective_user.id
    pending = _pending_rules.get(owner_id, {})
    if not pending.get("source") or not pending.get("target"):
        await update.effective_message.reply_text("请先设置来源和目标：/source <聊天>，/target <聊天>。")
        return
    try:
        inserted = context.application.bot_data["store"].add_rule(owner_id, pending["source"], pending["target"])
    except ValueError as exc:
        await update.effective_message.reply_text(f"规则未保存：{exc}")
        return
    _pending_rules.pop(owner_id, None)
    await update.effective_message.reply_text("规则已保存。" if inserted else "这条规则已经存在。")


async def list_rules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    rows = context.application.bot_data["store"].rules_for(update.effective_user.id)
    if not rows:
        await update.effective_message.reply_text("还没有转发规则。用 /source、/target、/save 创建一条。")
        return
    lines = [f"{i}. {source} → {target} ({'启用' if enabled else '停用'})" for i, source, target, enabled in rows]
    await update.effective_message.reply_text("已保存的规则：\n" + "\n".join(lines))


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    _pending_rules.pop(update.effective_user.id, None)
    context.user_data.pop("managed_flow", None)
    await update.effective_message.reply_text("已取消未保存的配置。")


async def add_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    if not context.args:
        await update.effective_message.reply_text("用法：/addtarget @频道用户名 或 /addtarget -100123456789")
        return
    try:
        chat = await context.bot.get_chat(context.args[0])
    except (BadRequest, Forbidden) as exc:
        await update.effective_message.reply_text(f"机器人访问不到目标。请先将机器人加入目标聊天并检查权限。\n{exc}")
        return
    title = chat.title or chat.username or str(chat.id)
    context.application.bot_data["store"].add_target(chat.id, title)
    await update.effective_message.reply_text(f"已添加目标：{title} ({chat.id})。发送时仍需具备发帖/发消息权限。")


async def list_targets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    targets = context.application.bot_data["store"].targets()
    if not targets:
        await update.effective_message.reply_text("还没有目标。使用 /addtarget <聊天> 添加。")
        return
    lines = [f"{i}. {title} ({chat_id})" for i, (chat_id, title) in enumerate(targets, 1)]
    await update.effective_message.reply_text("可用目标：\n" + "\n".join(lines) + "\n\n移除：/removetarget <聊天 ID>")


async def remove_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    if not context.args:
        await update.effective_message.reply_text("用法：/removetarget -100123456789")
        return
    removed = context.application.bot_data["store"].remove_target(context.args[0])
    await update.effective_message.reply_text("目标已移除。" if removed else "没有找到这个目标。")


def _target_keyboard(targets: list[tuple[str, str]], pending_id: str, sent: set[str]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(("✅ " if chat_id in sent else "发送到：") + title[:50], callback_data=f"send:{pending_id}:{chat_id}")]
        for chat_id, title in targets
    ]
    rows.append([InlineKeyboardButton("完成", callback_data=f"done:{pending_id}")])
    return InlineKeyboardMarkup(rows)


def _account_keyboard(pending_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🤖 机器人账号", callback_data=f"account:{pending_id}:bot"),
            InlineKeyboardButton("👤 个人 Telegram 账号", callback_data=f"account:{pending_id}:user"),
        ],
        [InlineKeyboardButton("取消", callback_data=f"done:{pending_id}")],
    ])


async def _copy_pending(application: Application, item: dict[str, object], target_id: str) -> int:
    message_ids = item["message_ids"]
    assert isinstance(message_ids, list)
    if len(message_ids) == 1:
        result = await application.bot.copy_message(
            chat_id=target_id,
            from_chat_id=item["source_chat"],
            message_id=message_ids[0],
        )
        return 1 if result else 0
    results = await application.bot.copy_messages(
        chat_id=target_id,
        from_chat_id=item["source_chat"],
        message_ids=message_ids,
    )
    return len(results)


async def _queue_personal_send(application: Application, item: dict[str, object], target_id: str) -> int:
    bot_username = application.bot_data.get("bot_username") or getattr(application.bot, "username", None)
    if not bot_username:
        bot_user = await application.bot.get_me()
        bot_username = bot_user.username
    if not bot_username:
        raise RuntimeError("Telegram did not provide the bot username needed for personal-account delivery.")
    message_ids = item["message_ids"]
    assert isinstance(message_ids, list)
    return application.bot_data["store"].create_user_task(
        int(item["owner_id"]),
        "direct",
        f"@{bot_username}",
        target_id,
        item_limit=len(message_ids),
        source_message_ids=message_ids,
        rule_snapshot=item.get("manual_rule"),
    )


async def _deliver_submission(application: Application, item: dict[str, object], target_id: str) -> tuple[int | None, str | None]:
    store: Store = application.bot_data["store"]
    rule = item.get("manual_rule")
    if rule:
        current = store.manual_rule(int(item["owner_id"]), int(rule["id"]))
        if current is None or not current["enabled"]:
            return None, "此普通转发规则已停用或删除，请重新选择规则并提交消息。"
        if target_id != rule["target_chat"] or target_id not in dict(store.targets()):
            return None, "规则目标已失效，请修改规则后重新提交。"
    if item.get("sender_account") == "user":
        if not application.bot_data.get("user_session_enabled") or store.user_session_account() != item["owner_id"]:
            return None, "个人账号未连接或与提交账号不一致，请恢复登录或重新选择发布账号。"
        try:
            task_id = await _queue_personal_send(application, item, target_id)
        except Exception as exc:
            logger.exception("Could not queue personal-account message delivery")
            return None, f"个人账号转发未能排队：{exc}"
        return task_id, None
    try:
        if rule:
            done = item.setdefault("delivered_ids", set())
            messages = [message for message in item["messages"] if message.message_id not in done]
            delivered = 0
            if rule["album_mode"] == "merge" and len(messages) > 1:
                if rule["sender_mode"] == "forward":
                    results = await application.bot.forward_messages(chat_id=target_id, from_chat_id=item["source_chat"], message_ids=[m.message_id for m in messages])
                else:
                    results = await application.bot.send_media_group(chat_id=target_id, media=[_media_for_group(m, rule) for m in messages])
                done.update(m.message_id for m in messages)
                delivered = len(results)
                store.record_activity(target_id, "success", delivered)
                if _message_button(rule) and results:
                    item["pending_button_message_id"] = results[-1].message_id
            else:
                for message in messages:
                    count = int(await _send_one(application, target_id, message, rule))
                    done.add(message.message_id)
                    delivered += count
                    if count:
                        store.record_activity(target_id, "success", count)
            if item.get("pending_button_message_id"):
                await application.bot.edit_message_reply_markup(chat_id=target_id, message_id=item["pending_button_message_id"], reply_markup=_message_button(rule))
                item.pop("pending_button_message_id", None)
            return delivered, None
        delivered = await _copy_pending(application, item, target_id)
    except TelegramError:
        store.record_activity(target_id, "failed", len(item["message_ids"]))
        logger.exception("Could not copy user-submitted messages to target %s", target_id)
        return None, "机器人账号发送失败，请检查 Bot 是否仍有目标频道的发布权限。"
    if not delivered:
        store.record_activity(target_id, "failed", len(item["message_ids"]))
        return None, "Telegram 没有复制这类消息。"
    store.record_activity(target_id, "success", delivered)
    return delivered, None


async def _show_submission_targets(application: Application, owner_id: int, pending_id: str, *, notice: str = "") -> None:
    item = application.bot_data.get("pending_copies", {}).get(pending_id)
    if item is None or item["owner_id"] != owner_id:
        return
    targets = application.bot_data["store"].targets()
    label = "机器人账号" if item.get("sender_account") == "bot" else "个人 Telegram 账号"
    await application.bot.send_message(
        owner_id,
        (notice + "\n\n" if notice else "")
        + f"已选择{label}。请选择一个或多个目标发送；完成后点“完成”。",
        reply_markup=_target_keyboard(targets, pending_id, item["sent"]),
    )


async def _offer_batch(
    application: Application,
    owner_id: int,
    source_chat: int,
    message_ids: list[int],
    album: bool,
    messages: list[Message] | None = None,
) -> None:
    store: Store = application.bot_data["store"]
    targets = store.targets()
    if not targets:
        await application.bot.send_message(owner_id, "还没有配置目标。管理员先用 /addtarget <聊天> 添加目标，再重新转发消息。")
        return

    pending_id = uuid4().hex[:12]
    pending = application.bot_data.setdefault("pending_copies", {})
    pending[pending_id] = {
        "owner_id": owner_id,
        "source_chat": source_chat,
        "message_ids": message_ids,
        "sent": set(),
        "sender_account": None,
    }
    selected_id = store.selected_manual_rule_id(owner_id)
    if selected_id is not None:
        rule = store.manual_rule(owner_id, selected_id)
        if not rule or not rule["enabled"]:
            pending.pop(pending_id, None)
            await application.bot.send_message(owner_id, "当前普通转发规则已停用或删除，消息未发送。请用 /manualrules 重新选择规则或切换快捷转发。")
            return
        if not messages:
            pending.pop(pending_id, None)
            await application.bot.send_message(owner_id, "未取得消息内容，请重新转发消息后应用规则。")
            return
        selected = (messages if _matches_album(messages, rule) else []) if album and rule["filter_scope"] == "album" else [m for m in messages if _matches_rule(m, rule)]
        if not selected:
            pending.pop(pending_id, None)
            await application.bot.send_message(owner_id, f"普通规则「{rule['name']}」：消息未通过筛选，已跳过。")
            return
        item = pending[pending_id]
        item.update(manual_rule=rule, messages=selected, message_ids=[m.message_id for m in selected], sender_account=rule["sender_account"])
        result, error = await _deliver_submission(application, item, str(rule["target_chat"]))
        if error:
            await application.bot.send_message(owner_id, f"普通规则「{rule['name']}」发送未完成：{error}", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("重试未完成部分", callback_data=f"send:{pending_id}:{rule['target_chat']}")],
                [InlineKeyboardButton("结束本次", callback_data=f"done:{pending_id}")],
            ]))
        else:
            pending.pop(pending_id, None)
            detail = f"个人账号任务 #{result} 已排队" if rule["sender_account"] == "user" else f"已发送 {result} 条消息"
            await application.bot.send_message(owner_id, f"普通规则「{rule['name']}」：{detail}。")
        return
    account = store.publishing_preference(owner_id)["sender_account"]
    if account:
        item = pending[pending_id]
        item["sender_account"] = account
        if account == "user" and (not application.bot_data.get("user_session_enabled") or store.user_session_account() != owner_id):
            item["sender_account"] = None
            await application.bot.send_message(owner_id, "默认个人账号当前未连接或与提交账号不一致。请恢复登录，或为这次消息选择发布账号。", reply_markup=_account_keyboard(pending_id))
            return
        target = store.default_target(owner_id)
        if target:
            result, error = await _deliver_submission(application, item, target)
            if not error:
                pending.pop(pending_id, None)
                title = dict(targets).get(target, target)
                text = f"个人账号转发任务 #{result} 已排队，目标：{title}。" if account == "user" else f"已由机器人账号复制到目标：{title}。"
                await application.bot.send_message(owner_id, text)
                return
            await _show_submission_targets(application, owner_id, pending_id, notice=f"默认目标无法发送：{error}")
            return
        await _show_submission_targets(application, owner_id, pending_id)
        return
    kind = f"包含 {len(message_ids)} 项的媒体相册" if album else "一条转发消息"
    await application.bot.send_message(
        owner_id,
        f"已收到{kind}。请选择发布账号；之后会发送到默认目标，或让你选择目标频道。\n"
        "机器人账号通过 Bot API 复制消息；个人账号需要使用已登录的同一 Telegram 账号提交消息。",
        reply_markup=_account_keyboard(pending_id),
    )


async def _flush_album(application: Application, key: tuple[int, str]) -> None:
    await asyncio.sleep(1.2)
    batch = _albums.pop(key, None)
    if not batch:
        return
    messages = batch["messages"]
    assert isinstance(messages, dict)
    await _offer_batch(
        application,
        int(batch["owner_id"]),
        int(batch["source_chat"]),
        sorted(int(message_id) for message_id in messages),
        True,
        messages=[messages[key] for key in sorted(messages)],
    )


async def _heartbeat_loop(application: Application) -> None:
    store: Store = application.bot_data["store"]
    while True:
        try:
            store.set_heartbeat(time.time())
        except Exception:
            logger.exception("Could not update dashboard heartbeat")
        await asyncio.sleep(15)


async def _application_started(application: Application) -> None:
    application.bot_data["store"].set_heartbeat(time.time())
    try:
        bot_user = await application.bot.get_me()
        application.bot_data["bot_username"] = bot_user.username
        await application.bot.set_my_commands([
            BotCommand("start", "打开主菜单"),
            BotCommand("rules", "管理实时转发规则"),
            BotCommand("newrule", "新建实时转发规则"),
            BotCommand("botforward", "直接转发消息到目标频道"),
            BotCommand("userforward", "选择发布账号"),
            BotCommand("publisher", "默认发布账号（30天）"),
            BotCommand("manualrules", "普通转发规则"),
            BotCommand("photo", "直接发送图片或相册"),
            BotCommand("history", "创建历史克隆任务"),
            BotCommand("extract", "使用消息链接提取"),
            BotCommand("tasks", "查看提取和克隆任务"),
            BotCommand("defaulttarget", "设置个人默认目标"),
            BotCommand("account", "查看账号会话和二维码登录"),
            BotCommand("targets", "查看目标频道"),
            BotCommand("addtarget", "添加目标频道"),
            BotCommand("cancel", "取消当前配置"),
        ])
        await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except TelegramError:
        logger.warning("Could not register Telegram menu commands", exc_info=True)
    application.bot_data["dashboard_heartbeat_task"] = asyncio.create_task(
        _heartbeat_loop(application), name="dashboard-heartbeat"
    )


async def _application_stopped(application: Application) -> None:
    task = application.bot_data.pop("dashboard_heartbeat_task", None)
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def receive_forward(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None or message.chat.type != "private":
        return
    if message.forward_origin is not None:
        context.user_data.pop("managed_flow", None)
    elif context.user_data.get("managed_flow"):
        return
    if message.media_group_id:
        key = (user.id, message.media_group_id)
        batch = _albums.setdefault(key, {"owner_id": user.id, "source_chat": message.chat_id, "messages": {}})
        messages = batch["messages"]
        assert isinstance(messages, dict)
        messages[message.message_id] = message
        if not batch.get("scheduled"):
            batch["scheduled"] = True
            context.application.create_task(_flush_album(context.application, key))
        return
    if message.forward_origin is None:
        link = parse_message_link(message.text or "")
        if link:
            if not context.application.bot_data.get("user_session_enabled"):
                await message.reply_text(
                    "链接提取需要启用用户账号会话：配置 API_ID/API_HASH，完成本地交互登录，并用 user-session Docker profile 启动。"
                )
                return
            targets = context.application.bot_data["store"].targets()
            if not targets:
                await message.reply_text("管理员还没有配置可用的转发目标。")
                return
            source, message_id = link
            task_id = context.application.bot_data["store"].create_user_task(
                user.id, "extract", source, source_message_id=message_id
            )
            rows = [
                [InlineKeyboardButton(title[:50], callback_data=f"extract:{task_id}:{chat_id}")]
                for chat_id, title in targets[:50]
            ]
            await message.reply_text("已识别消息链接。选择一个目标开始提取：", reply_markup=InlineKeyboardMarkup(rows))
            return
        if message.photo:
            await _offer_batch(context.application, user.id, message.chat_id, [message.message_id], False, messages=[message])
            return
        await message.reply_text("请在 Telegram 原消息上选择“转发”，然后发送到此机器人。图片也可以直接发送；仅发送普通链接不能提取消息。")
        return

    await _offer_batch(context.application, user.id, message.chat_id, [message.message_id], False, messages=[message])


async def copy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    parts = (query.data or "").split(":")
    pending = context.application.bot_data.get("pending_copies", {})
    if not parts or parts[0] not in {"send", "done", "account"} or len(parts) < 2:
        await query.answer()
        return
    item = pending.get(parts[1])
    if item is None or item["owner_id"] != query.from_user.id:
        await query.edit_message_text("这条待处理任务已失效，请重新转发消息。")
        await query.answer()
        return
    if parts[0] == "done":
        pending.pop(parts[1], None)
        await query.edit_message_text("处理完成。")
        await query.answer()
        return
    if parts[0] == "account":
        if item.get("manual_rule"):
            await query.answer("此消息使用普通转发规则内的发布账号。", show_alert=True)
            return
        if len(parts) != 3 or parts[2] not in {"bot", "user"}:
            await query.answer()
            return
        sender_account = parts[2]
        if sender_account == "user":
            if not context.application.bot_data.get("user_session_enabled"):
                await query.answer("个人账号会话尚未启用，请先完成会话登录并启动 user-session 服务。", show_alert=True)
                return
            session_account = context.application.bot_data["store"].user_session_account()
            if session_account is None:
                await query.answer("个人账号服务尚未连接，请检查登录状态并启动 user-session 服务后重试。", show_alert=True)
                return
            if session_account != query.from_user.id:
                await query.answer(
                    "已登录的个人 Telegram 账号与这条消息的提交账号不一致。请用同一账号登录，或选择机器人账号。",
                    show_alert=True,
                )
                return
        item["sender_account"] = sender_account
        await query.answer("个人账号任务会在后台处理。" if sender_account == "user" else "已选择机器人账号。")
        default_target = context.application.bot_data["store"].default_target(query.from_user.id)
        valid_targets = {chat_id for chat_id, _ in context.application.bot_data["store"].targets()}
        if default_target in valid_targets:
            result, error = await _deliver_submission(context.application, item, default_target)
            if error:
                await query.edit_message_text(f"默认目标无法发送：{error}\n请选择另一个目标，或返回主菜单。")
                await _show_submission_targets(
                    context.application,
                    query.from_user.id,
                    parts[1],
                )
                return
            item["sent"].add(default_target)
            title = dict(context.application.bot_data["store"].targets()).get(default_target, default_target)
            context.application.bot_data.get("pending_copies", {}).pop(parts[1], None)
            if sender_account == "user":
                await query.edit_message_text(f"个人账号转发任务 #{result} 已排队，目标：{title}。")
            else:
                await query.edit_message_text(f"已由机器人账号复制到目标：{title}。")
            return
        await query.edit_message_text("已选择发布账号。")
        await _show_submission_targets(context.application, query.from_user.id, parts[1])
        return
    if len(parts) != 3:
        await query.answer()
        return
    target_id = parts[2]
    if "sender_account" in item and item["sender_account"] is None:
        await query.answer("请先选择发布账号。", show_alert=True)
        return
    configured_target_ids = {chat_id for chat_id, _ in context.application.bot_data["store"].targets()}
    if target_id not in configured_target_ids:
        await query.answer("这个目标已移除或不可用，请重新提交消息。", show_alert=True)
        return
    if target_id in item["sent"]:
        await query.answer("这条消息已经发送到此目标。")
        return

    try:
        result, error = await _deliver_submission(context.application, item, target_id)
        if error:
            if item.get("sender_account") == "user":
                await query.answer(error, show_alert=True)
            elif error == "Telegram 没有复制这类消息。":
                await query.answer(error, show_alert=True)
            else:
                await query.answer("发送失败，请稍后重试并检查目标权限。", show_alert=True)
            return
        delivered_count = 1 if item.get("sender_account") == "user" else int(result or 0)
        if delivered_count == 0:
            await query.answer("Telegram 没有复制这类消息。", show_alert=True)
            return
    except RetryAfter as exc:
        await query.answer(f"Telegram 限流，请 {exc.retry_after} 秒后重试。", show_alert=True)
        return
    except (BadRequest, Forbidden) as exc:
        await query.answer(f"发送失败，请检查目标权限或消息是否受保护：{exc}", show_alert=True)
        return
    except TelegramError:
        logger.exception("Telegram API error while copying user-submitted messages to %s", target_id)
        await query.answer("Telegram 发送失败，请稍后重试。", show_alert=True)
        return

    item["sent"].add(target_id)
    if item.get("manual_rule"):
        pending.pop(parts[1], None)
        await query.edit_message_text("普通转发规则任务已提交。" if item["sender_account"] == "user" else "普通转发规则处理完成。")
        await query.answer()
        return
    targets = context.application.bot_data["store"].targets()
    await query.edit_message_reply_markup(reply_markup=_target_keyboard(targets, parts[1], item["sent"]))
    if item.get("sender_account") == "user":
        await query.answer(f"个人账号发送任务 #{result} 已排队。")
    else:
        await query.answer(f"已复制 {delivered_count} 条消息。")


async def relay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or message.chat is None:
        return
    if message.media_group_id:
        key = (message.chat_id, message.media_group_id)
        batch = _relay_albums.setdefault(key, {"messages": {}, "task": None})
        messages = batch["messages"]
        assert isinstance(messages, dict)
        messages[message.message_id] = message
        previous_task = batch.get("task")
        if isinstance(previous_task, asyncio.Task):
            previous_task.cancel()
        batch["task"] = context.application.create_task(_flush_relay_album(context.application, key))
        return

    await _relay_messages(context.application, [message])


async def _flush_relay_album(application: Application, key: tuple[int, str]) -> None:
    try:
        await asyncio.sleep(1.2)
    except asyncio.CancelledError:
        return
    batch = _relay_albums.pop(key, None)
    if not batch:
        return
    messages = batch["messages"]
    assert isinstance(messages, dict)
    await _relay_messages(application, [messages[key] for key in sorted(messages)])


def _message_media_types(message: Message) -> set[str]:
    kinds: set[str] = set()
    if message.text is not None:
        kinds.add("text")
    for attribute, kind in (
        ("photo", "photo"),
        ("video", "video"),
        ("document", "document"),
        ("audio", "audio"),
        ("animation", "animation"),
        ("voice", "voice"),
        ("video_note", "video_note"),
        ("sticker", "sticker"),
    ):
        if getattr(message, attribute, None) is not None:
            kinds.add(kind)
    return kinds


def _matches_rule(message: Message, rule: dict[str, object]) -> bool:
    media_types = rule["media_types"]
    assert isinstance(media_types, list)
    if "all" not in media_types and not _message_media_types(message).intersection(media_types):
        return False
    keywords = rule["filter_keywords"]
    assert isinstance(keywords, list)
    searchable_text = f"{message.text or ''}\n{message.caption or ''}".casefold()
    if keywords and not any(str(keyword).casefold() in searchable_text for keyword in keywords):
        return False
    exclude_keywords = rule.get("exclude_keywords", [])
    assert isinstance(exclude_keywords, list)
    return not any(str(keyword).casefold() in searchable_text for keyword in exclude_keywords)


def _matches_album(messages: list[Message], rule: dict[str, object]) -> bool:
    media_types = rule["media_types"]
    assert isinstance(media_types, list)
    album_types = set().union(*(_message_media_types(message) for message in messages))
    if "all" not in media_types and not album_types.intersection(media_types):
        return False
    searchable_text = "\n".join(
        f"{message.text or ''}\n{message.caption or ''}" for message in messages
    ).casefold()
    include = rule["filter_keywords"]
    exclude = rule.get("exclude_keywords", [])
    assert isinstance(include, list) and isinstance(exclude, list)
    if include and not any(str(keyword).casefold() in searchable_text for keyword in include):
        return False
    return not any(str(keyword).casefold() in searchable_text for keyword in exclude)


def _caption_options(message: Message, rule: dict[str, object]) -> dict[str, object]:
    mode = str(rule["caption_mode"])
    text = str(rule["caption_text"])
    if message.caption is None and not (_message_media_types(message) & {"photo", "video", "document", "audio", "animation", "voice"}):
        return {}
    options: dict[str, object] = {}
    current_caption = message.caption or ""
    current_entities = list(message.caption_entities or [])
    if mode == "remove":
        current_caption = ""
        current_entities = []
        options["caption"] = current_caption
        options["caption_entities"] = []
    elif mode == "replace":
        current_caption = text
        current_entities = []
        options["caption"] = current_caption
        options["caption_entities"] = []
    elif mode in {"prepend", "append"}:
        separator = "\n\n" if current_caption else ""
        if mode == "prepend":
            prefix = text + separator
            offset = len(prefix.encode("utf-16-le")) // 2
            shifted = []
            for entity in current_entities:
                data = entity.to_dict()
                data["offset"] += offset
                shifted.append(MessageEntity.de_json(data, message.get_bot()))
            current_entities = shifted
            current_caption = prefix + current_caption
        else:
            current_caption = current_caption + separator + text
        options["caption"] = current_caption
        if current_entities:
            options["caption_entities"] = current_entities

    if _has_text_edits(rule):
        transformed, entities = transform_text(current_caption, current_entities, rule)
        if transformed != current_caption:
            current_caption = transformed
            options["caption"] = transformed
            options["caption_entities"] = [
                MessageEntity.de_json(entity, message.get_bot()) for entity in entities
            ]
    return options


def _has_text_edits(rule: dict[str, object]) -> bool:
    return any((
        rule.get("remove_links", False),
        rule.get("remove_keywords", []),
        rule.get("replacements", []),
        rule.get("prefix_text", ""),
        rule.get("suffix_text", ""),
        rule.get("caption_mode", "keep") != "keep",
    ))


def _message_button(rule: dict[str, object]) -> InlineKeyboardMarkup | None:
    text = str(rule.get("button_text", ""))
    url = str(rule.get("button_url", ""))
    if not text or not url:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, url=url)]])


def _text_content_options(message: Message, rule: dict[str, object]) -> tuple[str, list[MessageEntity], bool]:
    text = message.text or ""
    entities = message.entities or []
    transformed, data = transform_text(text, entities, rule)
    updated = [MessageEntity.de_json(item, message.get_bot()) for item in data]
    return transformed, updated, transformed != text


def _media_for_group(message: Message, rule: dict[str, object]) -> object:
    caption_options = _caption_options(message, rule)
    caption = caption_options.get("caption", message.caption)
    entities = caption_options.get("caption_entities", message.caption_entities)
    common = {"caption": caption, "caption_entities": entities}
    if message.photo:
        return InputMediaPhoto(media=message.photo[-1].file_id, **common)
    if message.video:
        return InputMediaVideo(media=message.video.file_id, **common)
    if message.document:
        return InputMediaDocument(media=message.document.file_id, **common)
    if message.audio:
        return InputMediaAudio(media=message.audio.file_id, **common)
    raise ValueError("This media type cannot be sent as part of an album.")


async def _send_one(application: Application, target: str, message: Message, rule: dict[str, object]) -> bool:
    button = _message_button(rule)
    if rule.get("sender_mode") == "forward":
        await application.bot.forward_message(
            chat_id=target,
            from_chat_id=message.chat_id,
            message_id=message.message_id,
        )
        return True
    if message.text is not None and _has_text_edits(rule):
        text, entities, changed = _text_content_options(message, rule)
        if changed:
            if not text.strip():
                return False
            await application.bot.send_message(
                chat_id=target,
                text=text,
                entities=entities or None,
                reply_markup=button,
            )
            return True
    await application.bot.copy_message(
        chat_id=target,
        from_chat_id=message.chat_id,
        message_id=message.message_id,
        reply_markup=button,
        **_caption_options(message, rule),
    )
    return True


async def _relay_messages(application: Application, messages: list[Message]) -> None:
    if not messages:
        return
    store: Store = application.bot_data["store"]
    rules = store.active_rules(messages[0].chat_id, "bot")
    for rule in rules:
        if rule.get("filter_scope") == "album" and len(messages) > 1:
            selected = messages if _matches_album(messages, rule) else []
        else:
            selected = [message for message in messages if _matches_rule(message, rule)]
        if not selected:
            continue
        target = str(rule["target_chat"])
        try:
            delivered_count = 0
            if rule["album_mode"] == "merge" and len(selected) > 1:
                for offset in range(0, len(selected), 10):
                    group = selected[offset : offset + 10]
                    if rule.get("sender_mode") == "forward":
                        results = await application.bot.forward_messages(
                            chat_id=target,
                            from_chat_id=group[0].chat_id,
                            message_ids=[item.message_id for item in group],
                        )
                    elif _has_text_edits(rule) or _message_button(rule):
                        results = await application.bot.send_media_group(
                            chat_id=target,
                            media=[_media_for_group(item, rule) for item in group],
                        )
                        button = _message_button(rule)
                        if button and results:
                            await application.bot.edit_message_reply_markup(
                                chat_id=target,
                                message_id=results[-1].message_id,
                                reply_markup=button,
                            )
                    else:
                        results = await application.bot.copy_messages(
                            chat_id=target,
                            from_chat_id=group[0].chat_id,
                            message_ids=[item.message_id for item in group],
                        )
                    delivered_count += len(results)
            else:
                for item in selected:
                    delivered_count += int(await _send_one(application, target, item, rule))
            if delivered_count:
                store.record_activity(target, "success", delivered_count)
        except RetryAfter as exc:
            store.record_activity(target, "failed", len(selected))
            logger.warning("Telegram rate limit while sending to %s; retry after %s seconds", target, exc.retry_after)
        except (BadRequest, Forbidden) as exc:
            store.record_activity(target, "failed", len(selected))
            logger.warning("Cannot copy rule %s to %s: %s", rule["id"], target, exc)
        except TelegramError:
            store.record_activity(target, "failed", len(selected))
            logger.exception("Telegram API error while copying to %s", target)


def build_application() -> Application:
    load_dotenv()
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token or token == "replace-with-botfather-token":
        raise RuntimeError("Set BOT_TOKEN in .env before starting the bot.")
    admin_ids = {int(part.strip()) for part in os.getenv("ADMIN_IDS", "").split(",") if part.strip()}
    if not admin_ids:
        raise RuntimeError("Set at least one numeric Telegram user ID in ADMIN_IDS in .env.")
    database_path = os.getenv("DATABASE_PATH", "data/forwarder.sqlite3")
    Path(database_path).parent.mkdir(parents=True, exist_ok=True)

    app = (
        ApplicationBuilder()
        .token(token)
        .post_init(_application_started)
        .post_shutdown(_application_stopped)
        .build()
    )
    app.bot_data["admin_ids"] = admin_ids
    app.bot_data["store"] = Store(database_path)
    app.bot_data["user_session_enabled"] = os.getenv("USER_SESSION_ENABLED", "0").strip().lower() in {"1", "true", "yes"}
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("source", set_source))
    app.add_handler(CommandHandler("target", set_target))
    app.add_handler(CommandHandler("save", save_rule))
    app.add_handler(CommandHandler("rules", list_rules))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("addtarget", add_target))
    app.add_handler(CommandHandler("targets", list_targets))
    app.add_handler(CommandHandler("removetarget", remove_target))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, receive_forward))
    app.add_handler(CallbackQueryHandler(copy_callback))
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL | filters.ChatType.GROUPS, relay))
    register_management_handlers(app)
    return app


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # HTTP request URLs contain the Bot API token, so keep httpx request logs out of the console.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    application = build_application()
    logger.info("Starting Telegram forwarding bot polling")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
