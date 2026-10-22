from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timezone
from urllib.parse import urlparse

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from .store import MEDIA_TYPES, Store

MEDIA_LABELS = {
    "all": "全部",
    "text": "文字",
    "photo": "图片",
    "video": "视频",
    "document": "文件",
    "audio": "音频",
    "animation": "动画",
    "voice": "语音",
    "video_note": "视频消息",
    "sticker": "贴纸",
}
CAPTION_LABELS = {
    "keep": "保留原说明",
    "remove": "移除原说明",
    "replace": "替换说明",
    "prepend": "添加说明前缀",
    "append": "添加说明后缀",
}
RULE_STEPS = [
    "source_mode", "sender_account", "source_chat", "target_chat", "media_types", "filter_scope", "filter_keywords",
    "exclude_keywords", "sender_mode", "caption_mode", "caption_text", "remove_keywords",
    "replacements", "prefix_text", "suffix_text", "remove_links", "button_text",
    "button_url", "album_mode", "name", "review",
]
TEXT_STEPS = {
    "source_chat", "filter_keywords", "exclude_keywords", "caption_text", "remove_keywords",
    "replacements", "prefix_text", "suffix_text", "button_text", "button_url", "name",
}
LIST_STEPS = {"filter_keywords", "exclude_keywords", "remove_keywords"}
DEFAULT_RULE = {
    "source_mode": "bot",
    "sender_account": "bot",
    "name": "",
    "enabled": True,
    "media_types": ["all"],
    "filter_keywords": [],
    "exclude_keywords": [],
    "filter_scope": "message",
    "sender_mode": "copy",
    "caption_mode": "keep",
    "caption_text": "",
    "remove_keywords": [],
    "replacements": [],
    "prefix_text": "",
    "suffix_text": "",
    "remove_links": False,
    "button_text": "",
    "button_url": "",
    "album_mode": "individual",
}


def _is_admin(user_id: int | None, context: ContextTypes.DEFAULT_TYPE) -> bool:
    return user_id is not None and user_id in context.application.bot_data["admin_ids"]


def _advanced_markup(context: ContextTypes.DEFAULT_TYPE) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📨 直接转发消息", callback_data="menu:new_bot"), InlineKeyboardButton("👤 个人账号转发规则", callback_data="menu:new_user")],
        [InlineKeyboardButton("🖼 图片转发", callback_data="menu:new_photo"), InlineKeyboardButton("🔄 实时转发", callback_data="menu:rules")],
        [InlineKeyboardButton("🔐 账号与登录", callback_data="menu:account"), InlineKeyboardButton("🎯 目标频道", callback_data="menu:targets")],
        [InlineKeyboardButton("➕ 新建规则", callback_data="menu:new"), InlineKeyboardButton("默认转发对象", callback_data="menu:default_target")],
        [InlineKeyboardButton("🗂 历史克隆", callback_data="menu:history"), InlineKeyboardButton("📥 消息提取", callback_data="menu:submit")],
        [InlineKeyboardButton("📋 任务记录", callback_data="menu:tasks")],
        [InlineKeyboardButton("➕ 添加目标", callback_data="menu:target_add"), InlineKeyboardButton("❔ 帮助", callback_data="menu:help")],
        [InlineKeyboardButton("清空未保存草稿", callback_data="menu:clear")],
    ]
    dashboard_url = os.getenv("DASHBOARD_URL", "").strip()
    if urlparse(dashboard_url).scheme == "https" and urlparse(dashboard_url).netloc:
        rows.append([InlineKeyboardButton("打开仪表盘", url=dashboard_url)])
    return InlineKeyboardMarkup(rows)


def _publisher_rows():
    return [
        [InlineKeyboardButton("🤖 机器人 · 默认30天", callback_data="menu:publisher_set:bot")],
        [InlineKeyboardButton("👤 个人账号 · 默认30天", callback_data="menu:publisher_set:user")],
    ]


def _home_markup(context):
    return InlineKeyboardMarkup([[InlineKeyboardButton("返回主菜单", callback_data="menu:home")]])


async def _show_manual_rules(update, context):
    store = context.application.bot_data["store"]
    owner = update.effective_user.id
    selected = store.selected_manual_rule_id(owner)
    rules = store.manual_rules(owner)
    rows = []
    for rule in rules[:20]:
        rule_id = rule["id"]
        label = ("✅ " if selected == rule_id else "") + str(rule["name"])[:32]
        rows.append([InlineKeyboardButton(label, callback_data=f"menu:manual_select:{rule_id}"), InlineKeyboardButton("编辑", callback_data=f"menu:manual_edit:{rule_id}")])
        rows.append([InlineKeyboardButton("停用" if rule["enabled"] else "启用", callback_data=f"menu:manual_toggle:{rule_id}"), InlineKeyboardButton("删除", callback_data=f"menu:manual_delete:{rule_id}")])
    rows.extend([
        [InlineKeyboardButton("新建普通转发规则", callback_data="menu:manual_new")],
        [InlineKeyboardButton("使用快捷转发（不套用规则）", callback_data="menu:manual_clear")],
        [InlineKeyboardButton("返回主菜单", callback_data="menu:home")],
    ])
    current = store.manual_rule(owner, selected) if selected is not None else None
    label = current["name"] if current and current["enabled"] else ("已失效，请重新选择" if selected is not None else "快捷转发")
    await update.effective_message.reply_text(
        f"普通转发规则 · 当前：{label}\n只处理你主动发来的消息，无需来源 ID。选中的规则使用自身发布账号、目标和处理设置；快捷转发使用30天默认账号与默认目标。保存后还需点击规则名称选择使用。",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def _show_publisher(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("managed_flow", None)
    store = context.application.bot_data.get("store")
    pref = store.publishing_preference(update.effective_user.id) if store else {}
    account = pref.get("sender_account")
    text = "先选择发布账号，保存后30天内无需重复选择。普通转发无需来源 ID。"
    if account:
        label = "机器人账号" if account == "bot" else "个人 Telegram 账号"
        expiry = datetime.fromtimestamp(pref["expires_at"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        text += f"\n当前：{label}，有效至 {expiry}。再次选择可续期30天。"
    elif pref.get("expired"):
        text += "\n上次的默认账号已到期，请重新选择。"
    rows = _publisher_rows()
    if pref.get("expires_at"):
        rows.append([InlineKeyboardButton("清除默认账号", callback_data="menu:publisher_clear")])
    rows.append([InlineKeyboardButton("返回主菜单", callback_data="menu:home")])
    await update.effective_message.reply_text(text, reply_markup=InlineKeyboardMarkup(rows))


async def send_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    context.user_data.pop("managed_flow", None)
    store = context.application.bot_data.get("store")
    pref = store.publishing_preference(user.id) if store else {}
    account = pref.get("sender_account")
    rows = []
    if not account:
        text = "先选择发布账号，默认有效期30天。将你有权转发的消息发给机器人即可，无需来源 ID。"
        if pref.get("expired"):
            text += " 上次选择已到期，请重新选择。"
        rows.extend(_publisher_rows())
    else:
        label = "机器人账号" if account == "bot" else "个人 Telegram 账号"
        text = f"当前发布账号：{label}。将你有权转发的消息发给机器人即可；设置默认目标后会自动发送。"
        if account == "user" and (not context.application.bot_data.get("user_session_enabled") or store.user_session_account() != user.id):
            text += " 当前个人账号不可用，请恢复登录或更换账号。"
        rows.append([InlineKeyboardButton("📨 开始转发", callback_data="menu:new_bot"), InlineKeyboardButton("🖼 图片 / 相册", callback_data="menu:new_photo")])
    rows.extend([
        [InlineKeyboardButton("📝 普通转发规则", callback_data="menu:manual")],
        [InlineKeyboardButton("发布账号 · 30天", callback_data="menu:publisher"), InlineKeyboardButton("默认转发对象", callback_data="menu:default_target")],
        [InlineKeyboardButton("📋 任务记录", callback_data="menu:tasks"), InlineKeyboardButton("📥 消息提取", callback_data="menu:submit")],
    ])
    if store:
        selected_id = store.selected_manual_rule_id(user.id)
        if selected_id is not None:
            selected_rule = store.manual_rule(user.id, selected_id)
            text = "普通规则模式：" + (str(selected_rule["name"]) if selected_rule and selected_rule["enabled"] else "规则已失效，请重新选择") + "。发布账号与目标以规则为准，默认账号设置用于快捷转发。"
            rows = [row for row in rows if not any((button.callback_data or "").startswith("menu:publisher_set:") for button in row)]
    if _is_admin(user.id, context):
        text = "管理菜单 · " + text
        rows.append([InlineKeyboardButton("⚙ 自动转发与管理", callback_data="menu:advanced")])
        dashboard_url = os.getenv("DASHBOARD_URL", "").strip()
        if urlparse(dashboard_url).scheme == "https" and urlparse(dashboard_url).netloc:
            rows.append([InlineKeyboardButton("打开网页工作台", url=dashboard_url)])
    rows.append([InlineKeyboardButton("使用说明", callback_data="menu:help")])
    markup = InlineKeyboardMarkup(rows)
    await message.reply_text(text, reply_markup=markup)


async def _show_direct_forward(message, context: ContextTypes.DEFAULT_TYPE, *, photo: bool = False) -> None:
    context.user_data.pop("managed_flow", None)
    text = (
        "直接发送图片或图片相册，也可以将原图片消息转发给机器人。"
        if photo else
        "在 Telegram 原消息上选择“转发”，发送给机器人即可。支持文字、图片、视频、文件和相册。"
    )
    await message.reply_text(
        text + "\n可先在“普通转发规则”中选用规则，按规则账号、目标与处理设置发送。快捷转发模式可默认记住发布账号30天，已设置默认转发对象时自动发送，未设置时显示目标选择。"
        "\n无需来源 ID。选择个人账号时，登录会话必须是提交这条消息的同一个账号。",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("普通转发规则", callback_data="menu:manual")],
            [InlineKeyboardButton("选择发布账号 · 默认30天", callback_data="menu:publisher")],
            [InlineKeyboardButton("🎯 设置默认转发对象", callback_data="menu:default_target")],
            [InlineKeyboardButton("返回主菜单", callback_data="menu:home")],
        ]),
    )


def register_management_handlers(application: Application) -> None:
    for command in ("botforward", "userforward", "publisher", "manualrules", "photo", "extract", "tasks", "defaulttarget", "account"):
        application.add_handler(CommandHandler(command, feature_command), group=-1)
    application.add_handler(CommandHandler("newrule", new_rule_command), group=-1)
    application.add_handler(CommandHandler("history", history_command), group=-1)
    application.add_handler(
        CallbackQueryHandler(task_callback, pattern=r"^(extract|history|task):"),
        group=-2,
    )
    application.add_handler(
        CallbackQueryHandler(menu_callback, pattern=r"^(menu|wiz):"),
        group=-1,
    )
    application.add_handler(
        MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, handle_flow_text),
        group=-1,
    )


async def new_rule_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return
    if not _is_admin(user.id, context):
        await message.reply_text("仅管理员可以创建实时转发规则。")
        return
    await _begin_rule(update, context)


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return
    if not _is_admin(user.id, context):
        await message.reply_text("仅管理员可以创建历史克隆任务。")
        return
    await _start_history(message, context)


async def feature_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return
    command = (message.text or "").split(maxsplit=1)[0].split("@", 1)[0].removeprefix("/").casefold()
    if command == "account" and not _is_admin(user.id, context):
        await message.reply_text("此功能仅管理员可用。")
        return
    if command == "botforward":
        await _show_direct_forward(message, context)
    elif command in {"userforward", "publisher"}:
        await _show_publisher(update, context)
    elif command == "manualrules":
        context.user_data.pop("managed_flow", None)
        await _show_manual_rules(update, context)
    elif command == "photo":
        await _show_direct_forward(message, context, photo=True)
    elif command == "account":
        await _show_account(message, context)
    elif command == "extract":
        context.user_data.pop("managed_flow", None)
        await message.reply_text("发送一条 Telegram 消息链接即可提取。也可把允许转发的消息直接转发给机器人。")
    elif command == "tasks":
        await _show_tasks(update, context)
    elif command == "defaulttarget":
        await _show_default_target(update, context)


async def _show_account(message, context: ContextTypes.DEFAULT_TYPE) -> None:
    enabled = bool(context.application.bot_data.get("user_session_enabled"))
    session_account = context.application.bot_data["store"].user_session_account() if enabled else None
    state = f"已连接（账号 ID：{session_account}）" if session_account is not None else ("已配置但未连接" if enabled else "尚未启用")
    await message.reply_text(
        f"用户账号会话：{state}\n"
        "二维码登录在运行 Docker 的主机终端执行：docker compose --profile user-session run --rm user-session python -m forwarder.user_session_login\n"
        "先在 .env 配置 API_ID/API_HASH。手机 Telegram 打开“设置 → 设备 → 连接桌面设备”扫描终端二维码；登录后启动 user-session profile。\n"
        "登录会话文件只保存在部署主机，请勿发送给他人。"
    )


async def _start_history(message, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.application.bot_data.get("user_session_enabled"):
        await message.reply_text(
            "历史克隆需要先配置 API_ID/API_HASH、完成本地用户会话登录，并启用 Docker user-session profile。"
        )
        return
    context.user_data["managed_flow"] = {"kind": "history", "step": "source"}
    await message.reply_text("发送要克隆的来源聊天数字 ID 或公开 @username。用户账号必须已加入私密来源。")


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if query is None or user is None:
        return
    data = query.data or ""
    if data.startswith("wiz:"):
        if not _is_admin(user.id, context) and context.user_data.get("managed_flow", {}).get("kind") != "manual":
            await query.answer("仅管理员可以配置转发规则。", show_alert=True)
            return
        await wizard_callback(update, context)
        return

    action = data.removeprefix("menu:")
    if action in {"advanced", "new", "new_user", "account", "rules", "targets", "target_add", "history", "clear"} or action.startswith(("rule:", "target_remove:")):
        if not _is_admin(user.id, context):
            await query.answer("此菜单仅管理员可用。", show_alert=True)
            return
    await query.answer()

    store: Store = context.application.bot_data["store"]
    if action == "home":
        await send_main_menu(update, context)
    elif action == "manual":
        context.user_data.pop("managed_flow", None)
        await _show_manual_rules(update, context)
    elif action == "manual_new":
        await _begin_rule(update, context, manual=True)
    elif action == "manual_clear":
        store.select_manual_rule(user.id, None)
        await _show_manual_rules(update, context)
    elif action.startswith(("manual_select:", "manual_edit:", "manual_toggle:", "manual_delete:", "manual_confirm:")):
        operation, raw_id = action.split(":", 1)
        if not raw_id.isdigit():
            return
        rule_id = int(raw_id)
        rule = store.manual_rule(user.id, rule_id)
        if rule is None:
            await query.message.reply_text("规则不存在，请重新打开普通转发规则。")
            return
        if operation == "manual_edit":
            await _begin_rule(update, context, rule=rule, manual=True)
            return
        if operation == "manual_delete":
            await query.message.reply_text(f"确认删除普通规则「{rule['name']}」？", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("确认删除", callback_data=f"menu:manual_confirm:{rule_id}"), InlineKeyboardButton("取消", callback_data="menu:manual")],
            ]))
            return
        try:
            if operation == "manual_select":
                store.select_manual_rule(user.id, rule_id)
            elif operation == "manual_toggle":
                store.save_manual_rule(user.id, {**rule, "enabled": not rule["enabled"]}, rule_id)
            elif operation == "manual_confirm":
                store.delete_manual_rule(user.id, rule_id)
        except ValueError as exc:
            await query.message.reply_text(str(exc))
        await _show_manual_rules(update, context)
    elif action == "advanced":
        await query.message.reply_text("自动转发与管理：持续监听才需要来源 ID。", reply_markup=_advanced_markup(context))
    elif action == "publisher":
        await _show_publisher(update, context)
    elif action == "publisher_clear":
        store.set_publishing_account(user.id, None)
        await _show_publisher(update, context)
    elif action.startswith("publisher_set:"):
        account = action.split(":", 1)[1]
        if account not in {"bot", "user"}:
            return
        if account == "user" and (not context.application.bot_data.get("user_session_enabled") or store.user_session_account() != user.id):
            await query.message.reply_text("个人账号尚未连接或与当前账号不一致。请使用同一 Telegram 账号登录，或选择机器人账号。")
            return
        store.set_publishing_account(user.id, account)
        await query.message.reply_text("默认发布账号已保存，有效30天。现在转发消息或发送图片即可；默认目标可在主菜单设置。")
        await send_main_menu(update, context)
    elif action == "new":
        await _begin_rule(update, context)
    elif action == "new_bot":
        await _show_direct_forward(query.message, context)
    elif action == "new_user":
        await _begin_rule(update, context, preset={"source_mode": "user", "sender_account": "user"})
    elif action == "new_photo":
        await _show_direct_forward(query.message, context, photo=True)
    elif action == "account":
        await _show_account(query.message, context)
    elif action == "rules":
        await _show_rules(update, context)
    elif action == "targets":
        await _show_targets(update, context)
    elif action == "target_add":
        context.user_data["managed_flow"] = {"kind": "target_add"}
        await query.message.reply_text("发送目标频道或群组的公开用户名（@name）或数字 ID。发送 /cancel 可取消。")
    elif action == "history":
        await _start_history(query.message, context)
    elif action == "default_target":
        await _show_default_target(update, context)
    elif action == "default_clear":
        store.set_default_target(user.id, None)
        await query.message.reply_text("默认转发对象已清除；以后转发时将再次显示目标选择。")
        await _show_default_target(update, context)
    elif action.startswith("default_set:"):
        target_id = action.split(":", 1)[1]
        target_title = dict(store.targets()).get(target_id)
        if target_title is None:
            await query.message.reply_text("该目标已移除，请重新选择。")
            await _show_default_target(update, context)
            return
        store.set_default_target(user.id, target_id)
        await query.message.reply_text(
            f"默认转发对象已设为 {target_title}。你之后手动转发消息给机器人并选择发布账号后，会自动发送到这里。"
        )
        await _show_default_target(update, context)
    elif action == "tasks":
        await _show_tasks(update, context)
    elif action == "submit":
        context.user_data.pop("managed_flow", None)
        await query.message.reply_text(
            "消息提取：发送消息链接可从可访问的公开或私密来源提取；也可从 Telegram 原消息菜单选择“转发”后发送给机器人。这里只能处理你有权访问且未开启内容保护的消息。"
        )
    elif action == "photo_submit":
        await _show_direct_forward(query.message, context, photo=True)
    elif action == "help":
        await query.message.reply_text(
            "直接转发消息：先在主菜单选发布账号（默认30天），再将原消息转发给机器人；设置默认目标后自动发送，否则选择频道。无需来源 ID。个人账号登录须与提交消息的账号一致。\n"
            "实时转发：自动监听来源的新消息，需要在“新建规则”中配置来源 ID 和目标。\n"
            "个人账号转发：由部署者登录的 Telegram 账号读取并发送，需有目标发布权限；需要先完成 user-session 登录。\n"
            "图片转发支持单张图片、图片相册和图片说明文字。管理员可在此新建规则、设置目标和历史克隆；仪表盘可管理全部规则细项。"
        )
    elif action == "clear":
        context.user_data.pop("managed_flow", None)
        await query.message.reply_text("未保存的配置草稿已清除。")
    elif action.startswith("rule:"):
        await _rule_action(update, context, action.split(":"))
    elif action.startswith("target_remove:"):
        await _remove_target(update, context, action.split(":", 1)[1])


async def task_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if query is None or user is None:
        return
    parts = (query.data or "").split(":")
    store: Store = context.application.bot_data["store"]
    if parts[0] == "extract" and len(parts) == 3:
        try:
            task_id = int(parts[1])
        except ValueError:
            await query.answer("提取请求无效。", show_alert=True)
            return
        target_chat = parts[2]
        task = store.user_task(task_id)
        if task is None or task["owner_id"] != user.id or task["status"] != "awaiting_target":
            await query.answer("提取请求已失效。", show_alert=True)
            return
        targets = {chat_id for chat_id, _ in store.targets()}
        if target_chat not in targets:
            await query.answer("此目标已移除，请重新提交链接。", show_alert=True)
            return
        store.set_user_task_target(task_id, user.id, target_chat)
        await query.answer("已加入队列")
        await query.message.reply_text(f"消息提取任务 #{task_id} 已排队。")
        return
    if parts[0] == "history" and len(parts) == 3 and parts[1] == "target":
        flow = context.user_data.get("managed_flow")
        if not _is_admin(user.id, context) or not flow or flow.get("kind") != "history":
            await query.answer("历史克隆配置已过期。", show_alert=True)
            return
        target_chat = parts[2]
        if target_chat not in {chat_id for chat_id, _ in store.targets()}:
            await query.answer("此目标已移除。", show_alert=True)
            return
        flow["target_chat"] = target_chat
        flow["step"] = "limit"
        await query.answer()
        await query.message.reply_text("请输入要克隆的最近消息数量（1–5000）。")
        return
    if parts[0] == "task" and len(parts) == 3:
        try:
            task_id = int(parts[1])
        except ValueError:
            await query.answer("任务编号无效。", show_alert=True)
            return
        task = store.user_task(task_id)
        if task is None or (task["owner_id"] != user.id and not _is_admin(user.id, context)):
            await query.answer("找不到这条任务。", show_alert=True)
            return
        action = parts[2]
        status = str(task["status"])
        if action == "pause" and status in {"queued", "running"}:
            store.set_user_task_status(task_id, "paused")
            answer = "已暂停"
        elif action == "resume" and status == "paused":
            store.set_user_task_status(task_id, "queued")
            answer = "已继续"
        elif action == "cancel" and status not in {"completed", "failed", "cancelled"}:
            store.set_user_task_status(task_id, "cancelled")
            answer = "已取消"
        else:
            await query.answer("当前状态不支持此操作。", show_alert=True)
            return
        await query.answer(answer)
        await query.message.reply_text(f"任务 #{task_id}{answer}。")
        return
    await query.answer()


async def _show_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message or update.callback_query.message
    user_id = update.effective_user.id
    store: Store = context.application.bot_data["store"]
    tasks = store.user_tasks(None if _is_admin(user_id, context) else user_id, 12)
    if not tasks:
        await message.reply_text("没有提取或克隆任务。", reply_markup=_home_markup(context) if _is_admin(user_id, context) else None)
        return
    labels = {
        "awaiting_target": "待选目标", "queued": "排队中", "running": "运行中", "paused": "已暂停",
        "completed": "已完成", "failed": "失败", "cancelled": "已取消",
    }
    rows = []
    lines = []
    for task in tasks:
        kind = {
            "extract": "链接提取",
            "history": "历史克隆",
            "direct": "个人账号转发",
        }.get(str(task["kind"]), str(task["kind"]))
        line = f"#{task['id']} · {kind} · {labels[str(task['status'])]} · {task['progress']}/{task['item_limit']}"
        if task["error"]:
            line += f"\n  {str(task['error'])[:120]}"
        lines.append(line)
        buttons = []
        if task["status"] in {"queued", "running"}:
            buttons.append(InlineKeyboardButton("暂停", callback_data=f"task:{task['id']}:pause"))
        if task["status"] == "paused":
            buttons.append(InlineKeyboardButton("继续", callback_data=f"task:{task['id']}:resume"))
        if task["status"] not in {"completed", "failed", "cancelled"}:
            buttons.append(InlineKeyboardButton("取消", callback_data=f"task:{task['id']}:cancel"))
        if buttons:
            rows.append(buttons)
    rows.append([InlineKeyboardButton("刷新任务", callback_data="menu:tasks")])
    await message.reply_text("任务记录：\n" + "\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))


async def _begin_rule(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    rule: dict[str, object] | None = None,
    preset: dict[str, object] | None = None,
    manual: bool = False,
) -> None:
    message = update.effective_message or (update.callback_query.message if update.callback_query else None)
    if message is None:
        return
    values = dict(DEFAULT_RULE)
    values["media_types"] = list(DEFAULT_RULE["media_types"])
    if rule:
        values.update(rule)
    if preset:
        values.update(preset)
    if not manual and values["sender_account"] == "user" and not context.application.bot_data.get("user_session_enabled"):
        await _show_account(message, context)
        return
    first_step = "sender_account" if manual else ("source_chat" if preset and preset.get("sender_account") == "user" else "source_mode")
    context.user_data["managed_flow"] = {
        "kind": "manual" if manual else "rule",
        "owner_id": update.effective_user.id,
        "rule_id": int(rule["id"]) if rule else None,
        "source_chat": str(rule.get("source_chat", "")) if rule else "",
        "target_chat": str(rule["target_chat"]) if rule else "",
        "settings": values,
        "step": first_step,
        "preset_account": preset.get("sender_account") if preset else None,
    }
    await _show_step(message, context, user_id=update.effective_user.id, step=first_step)


def _step_markup(context: ContextTypes.DEFAULT_TYPE, step: str) -> InlineKeyboardMarkup | None:
    flow = context.user_data.get("managed_flow", {})
    settings = flow.get("settings", {})
    if step == "target_chat":
        targets = context.application.bot_data["store"].targets()
        buttons = [
            [InlineKeyboardButton(("✓ " if chat_id == flow.get("target_chat") else "") + title[:48], callback_data=f"wiz:target:{chat_id}")]
            for chat_id, title in targets
        ]
        if flow.get("rule_id"):
            buttons.append([InlineKeyboardButton("保留当前目标", callback_data="wiz:skip")])
        buttons.append([InlineKeyboardButton("取消", callback_data="wiz:cancel")])
        return InlineKeyboardMarkup(buttons)
    if step == "media_types":
        selected = set(settings.get("media_types", ["all"]))
        choices = ["all", "text", "photo", "video", "document", "audio", "animation", "voice", "video_note", "sticker"]
        rows = []
        for index in range(0, len(choices), 2):
            rows.append([
                InlineKeyboardButton(
                    ("✓ " if choice in selected else "") + MEDIA_LABELS[choice],
                    callback_data=f"wiz:media:{choice}",
                )
                for choice in choices[index:index + 2]
            ])
        rows.append([InlineKeyboardButton("继续", callback_data="wiz:next"), InlineKeyboardButton("取消", callback_data="wiz:cancel")])
        return InlineKeyboardMarkup(rows)
    options = {
        "filter_scope": [("message", "逐条消息"), ("album", "整个相册")],
        "source_mode": [("bot", "Bot API 来源"), ("user", "用户账号来源")],
        "sender_account": [("bot", "🤖 机器人账号发送"), ("user", "👤 个人账号发送")],
        "sender_mode": [("copy", "复制并隐藏来源"), ("forward", "保留转发来源")],
        "caption_mode": list(CAPTION_LABELS.items()),
        "remove_links": [("yes", "移除链接"), ("no", "保留链接")],
        "album_mode": [("individual", "逐条发送"), ("merge", "合并为相册")],
        "review": [("save", "保存规则")],
    }
    if step in options:
        choices = options[step]
        rows = [
            [InlineKeyboardButton(label, callback_data=f"wiz:set:{step}:{value}") for value, label in choices[index:index + 2]]
            for index in range(0, len(choices), 2)
        ]
        if step != "review":
            rows.append([InlineKeyboardButton("取消", callback_data="wiz:cancel")])
        else:
            rows.append([InlineKeyboardButton("取消配置", callback_data="wiz:cancel")])
        return InlineKeyboardMarkup(rows)
    if step in TEXT_STEPS:
        rows = []
        if flow.get("rule_id") or step != "source_chat":
            rows.append([InlineKeyboardButton("跳过 / 保留当前值", callback_data="wiz:skip")])
        if flow.get("rule_id") and step not in {"source_chat", "name"}:
            rows.append([InlineKeyboardButton("清空此项", callback_data="wiz:clear")])
        rows.append([InlineKeyboardButton("取消", callback_data="wiz:cancel")])
        return InlineKeyboardMarkup(rows)
    return InlineKeyboardMarkup([[InlineKeyboardButton("取消", callback_data="wiz:cancel")]])


def _step_prompt(flow: dict[str, object], step: str) -> str:
    settings = flow["settings"]
    prompts = {
        "source_chat": "发送来源频道/群组的公开用户名或数字 ID。",
        "filter_keywords": "发送包含关键词，每行或逗号分隔；命中任意词时通过。留空表示不过滤。",
        "exclude_keywords": "发送排除关键词，每行或逗号分隔；命中任意词就跳过。留空表示不排除。",
        "caption_text": "发送要替换或添加到媒体说明中的文字。",
        "remove_keywords": "发送要从消息正文和说明中删除的词，每行或逗号分隔。",
        "replacements": "逐行发送查找与替换，格式：原文字 => 新文字。",
        "prefix_text": "发送要加到消息正文和媒体说明开头的文字。",
        "suffix_text": "发送要加到消息正文和媒体说明结尾的文字。",
        "button_text": "发送底部 URL 按钮文字；不需要按钮可点跳过。",
        "button_url": "发送底部按钮的 http:// 或 https:// 链接。",
        "name": "给规则起一个名称。",
    }
    if step == "target_chat":
        return "选择一个已保存的目标频道/群组。" if flow.get("rule_id") else "选择规则要发送到的目标频道/群组。"
    if step == "source_chat":
        if settings.get("source_mode") == "user":
            return "发送来源数字 ID 或公开 @username。私密来源必须已在登录账号的聊天列表中。"
        return "发送来源数字 ID 或公开 @username。Bot API 模式要求机器人能收到该来源的新消息。"
    if step == "media_types":
        return "选择消息类型，可多选；完成后点“继续”。"
    if step == "filter_scope":
        return "选择关键词和媒体类型按单条消息还是整个相册判断。"
    if step == "sender_mode":
        return "选择发送形式。复制会隐藏来源；转发会保留 Telegram 来源信息。"
    if step == "sender_account":
        if flow.get("kind") == "manual":
            return "选择普通转发规则的发布账号。个人账号须与提交消息的账号一致；无需填写来源 ID。"
        return "选择发送账号。个人账号发送需要用户账号来源及目标发布权限；频道内显示身份由 Telegram 频道设置决定。"
    if step == "caption_mode":
        return "选择媒体说明文字的处理方式。"
    if step == "remove_links":
        return "是否从消息正文和媒体说明中移除链接？"
    if step == "album_mode":
        return "选择相册中的多条媒体如何发送。"
    if step == "review":
        return _rule_summary(flow)
    if step == "source_mode":
        return "选择来源读取方式。用户账号模式需要先完成本地 Telegram 账号登录。"
    current = settings.get(step)
    if flow.get("rule_id") and current not in (None, "", [], False):
        return prompts.get(step, "请输入配置值") + f"\n当前值：{current}"
    return prompts.get(step, "请输入配置值")


async def _show_step(message, context: ContextTypes.DEFAULT_TYPE, user_id: int, step: str) -> None:
    flow = context.user_data.get("managed_flow")
    if not flow:
        return
    flow["step"] = step
    await message.reply_text(_step_prompt(flow, step), reply_markup=_step_markup(context, step))


def _visible_steps(flow: dict[str, object]) -> list[str]:
    settings = flow["settings"]
    steps = list(RULE_STEPS)
    if flow.get("kind") == "manual":
        steps = [step for step in steps if step not in {"source_mode", "source_chat"}]
    elif settings.get("source_mode") == "bot" or flow.get("preset_account"):
        steps.remove("sender_account")
    if settings.get("sender_account") == "user":
        steps = [step for step in steps if step not in {"button_text", "button_url"}]
    if settings.get("sender_mode") == "forward":
        skipped = {"caption_mode", "caption_text", "remove_keywords", "replacements", "prefix_text", "suffix_text", "remove_links", "button_text", "button_url"}
        steps = [step for step in steps if step not in skipped]
    if "caption_text" in steps and settings.get("caption_mode") not in {"replace", "prepend", "append"}:
        steps.remove("caption_text")
    if "button_url" in steps and not settings.get("button_text"):
        steps.remove("button_url")
    return steps


async def _advance(message, context: ContextTypes.DEFAULT_TYPE) -> None:
    flow = context.user_data.get("managed_flow")
    if not flow:
        return
    steps = _visible_steps(flow)
    current = flow["step"]
    index = steps.index(current)
    await _show_step(message, context, user_id=0, step=steps[min(index + 1, len(steps) - 1)])


async def handle_flow_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    flow = context.user_data.get("managed_flow")
    if message is None or user is None or not flow:
        return
    # Forwarded content is a submission, never a wizard field value.
    if message.forward_origin is not None:
        context.user_data.pop("managed_flow", None)
        return
    if not _is_admin(user.id, context) and flow.get("kind") != "manual":
        context.user_data.pop("managed_flow", None)
        return
    if flow.get("kind") == "target_add":
        await _handle_target_text(message, context)
        return
    if flow.get("kind") == "history":
        if message.text is None:
            await message.reply_text("请直接发送文字内容。")
            return
        if flow.get("step") == "source":
            raw = message.text.strip()
            if not (re.fullmatch(r"-?\d{1,20}", raw) or re.fullmatch(r"@[A-Za-z0-9_]{5,32}", raw)):
                await message.reply_text("来源请填写数字聊天 ID 或公开 @username。")
                return
            flow["source_chat"] = raw
            targets = context.application.bot_data["store"].targets()
            if not targets:
                context.user_data.pop("managed_flow", None)
                await message.reply_text("请先在主菜单添加一个目标频道。")
                return
            rows = [[InlineKeyboardButton(title[:48], callback_data=f"history:target:{chat_id}")] for chat_id, title in targets]
            await message.reply_text("选择历史克隆的目标：", reply_markup=InlineKeyboardMarkup(rows))
            return
        if flow.get("step") == "limit":
            try:
                limit = int(message.text.strip())
            except ValueError:
                await message.reply_text("请输入 1 到 5000 之间的消息数量。")
                return
            if not 1 <= limit <= 5000:
                await message.reply_text("消息数量需在 1 到 5000 之间。")
                return
            task_id = context.application.bot_data["store"].create_user_task(
                user.id, "history", str(flow["source_chat"]), str(flow["target_chat"]), item_limit=limit
            )
            context.user_data.pop("managed_flow", None)
            await message.reply_text(f"历史克隆任务 #{task_id} 已排队。可在主菜单“任务记录”中暂停、继续或取消。")
            return
    if flow.get("kind") not in {"rule", "manual"}:
        return
    step = flow.get("step")
    if step not in TEXT_STEPS:
        await message.reply_text("请使用上方按钮选择，然后继续。")
        return
    if message.text is None:
        await message.reply_text("此步骤需要文字输入，请直接发送文字。")
        return
    text = message.text
    settings = flow["settings"]
    if step == "source_chat":
        raw = text.strip()
        if flow["settings"].get("source_mode") == "user":
            if not (re.fullmatch(r"-?\d{1,20}", raw) or re.fullmatch(r"@[A-Za-z0-9_]{5,32}", raw)):
                await message.reply_text("用户账号模式请填写数字聊天 ID 或公开 @username。")
                return
            flow["source_chat"] = raw
        else:
            try:
                chat = await context.bot.get_chat(raw)
            except (BadRequest, Forbidden) as exc:
                await message.reply_text(f"无法解析来源。Bot API 模式需让机器人加入来源并能读取新消息。\n{exc}")
                return
            flow["source_chat"] = str(chat.id)
    elif step in LIST_STEPS:
        settings[step] = [part.strip() for part in text.replace(",", "\n").splitlines() if part.strip()]
    elif step == "replacements":
        replacements = []
        for line in text.splitlines():
            if not line.strip():
                continue
            if "=>" not in line:
                await message.reply_text("格式错误。每行请使用：原文字 => 新文字。")
                return
            find, replacement = line.split("=>", 1)
            if not find.strip():
                await message.reply_text("查找文字不能为空。")
                return
            replacements.append({"find": find.strip(), "replace": replacement.strip()})
        settings[step] = replacements
    else:
        value = text.strip()
        if step in {"prefix_text", "suffix_text", "caption_text"}:
            value = text
        settings[step] = value
        if step == "button_text" and not value:
            settings["button_url"] = ""
    await _advance(message, context)


async def _handle_target_text(message, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw = (message.text or "").strip()
    if not raw:
        await message.reply_text("请发送公开用户名或数字聊天 ID。")
        return
    try:
        chat = await context.bot.get_chat(raw)
    except (BadRequest, Forbidden) as exc:
        await message.reply_text(f"机器人无法解析或访问这个目标，请确认用户名/ID。\n{exc}")
        return
    title = chat.title or chat.username or str(chat.id)
    context.application.bot_data["store"].add_target(chat.id, title)
    context.user_data.pop("managed_flow", None)
    await message.reply_text(f"目标已保存：{title}（{chat.id}）。发送 /start 返回管理菜单。")


async def wizard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    flow = context.user_data.get("managed_flow")
    if not flow or flow.get("kind") not in {"rule", "manual"}:
        await query.answer("这份配置已过期，请从主菜单重新开始。", show_alert=True)
        return
    data = (query.data or "").split(":")
    action = data[1] if len(data) > 1 else ""
    store: Store = context.application.bot_data["store"]
    settings = flow["settings"]
    await query.answer()
    if action == "cancel":
        context.user_data.pop("managed_flow", None)
        await query.message.reply_text("未保存的规则配置已取消。", reply_markup=_home_markup(context))
        return
    if action == "target" and len(data) == 3:
        valid_ids = {chat_id for chat_id, _ in store.targets()}
        if data[2] not in valid_ids:
            await query.message.reply_text("此目标已移除，请重新选择。")
            return
        flow["target_chat"] = data[2]
        await _advance(query.message, context)
        return
    if action == "media" and len(data) == 3:
        choice = data[2]
        if choice not in MEDIA_TYPES:
            return
        selected = set(settings.get("media_types", ["all"]))
        if choice == "all":
            selected = set() if "all" in selected else {"all"}
        else:
            selected.discard("all")
            if choice in selected:
                selected.remove(choice)
            else:
                selected.add(choice)
            if not selected:
                selected = {"all"}
        settings["media_types"] = [value for value in ["all", "text", "photo", "video", "document", "audio", "animation", "voice", "video_note", "sticker"] if value in selected]
        await query.message.reply_text("当前选择：" + "、".join(MEDIA_LABELS[item] for item in settings["media_types"]), reply_markup=_step_markup(context, "media_types"))
        return
    if action == "set" and len(data) == 4:
        _, _, step, value = data
        allowed = {
            "source_mode": {"bot", "user"},
            "sender_account": {"bot", "user"},
            "filter_scope": {"message", "album"},
            "sender_mode": {"copy", "forward"},
            "caption_mode": set(CAPTION_LABELS),
            "remove_links": {"yes", "no"},
            "album_mode": {"individual", "merge"},
            "review": {"save"},
        }
        if step not in allowed or value not in allowed[step]:
            return
        if step != flow.get("step"):
            await query.message.reply_text("请使用当前步骤的按钮。")
            return
        if step in {"source_mode", "sender_account"} and value == "user" and not context.application.bot_data.get("user_session_enabled"):
            await _show_account(query.message, context)
            return
        if step == "sender_mode" and value == "forward" and settings.get("source_mode") == "user" and settings.get("sender_account") == "bot":
            await query.message.reply_text("用户账号读取后由 Bot 发布仅支持隐藏来源复制，请选择复制。")
            return
        if step == "review" and value == "save":
            await _save_rule(query.message, context)
            return
        settings[step] = value == "yes" if step == "remove_links" else value
        if flow.get("kind") == "manual" and step == "sender_account":
            settings["source_mode"] = value
        if step == "source_mode" and value == "bot":
            settings["sender_account"] = "bot"
        if step == "sender_account" and value == "user":
            settings.update({"button_text": "", "button_url": ""})
        if step == "sender_mode" and value == "forward":
            settings.update({
                "caption_mode": "keep", "caption_text": "", "remove_keywords": [], "replacements": [],
                "prefix_text": "", "suffix_text": "", "remove_links": False,
                "button_text": "", "button_url": "",
            })
        await _advance(query.message, context)
        return
    if action == "next":
        if flow.get("step") == "media_types" and not settings.get("media_types"):
            settings["media_types"] = ["all"]
        await _advance(query.message, context)
        return
    if action == "skip":
        if flow.get("step") == "source_chat" and not flow.get("source_chat"):
            await query.message.reply_text("来源不能为空，请发送来源频道/群组 ID 或用户名。")
            return
        if flow.get("step") == "target_chat" and not flow.get("target_chat"):
            await query.message.reply_text("目标不能为空，请选择一个已保存目标。")
            return
        await _advance(query.message, context)
    elif action == "clear":
        step = str(flow.get("step"))
        if step in LIST_STEPS or step == "replacements":
            settings[step] = []
        elif step in TEXT_STEPS:
            settings[step] = ""
        if step == "button_text":
            settings["button_url"] = ""
        await _advance(query.message, context)


async def _save_rule(message, context: ContextTypes.DEFAULT_TYPE) -> None:
    flow = context.user_data.get("managed_flow")
    if not flow:
        return
    store: Store = context.application.bot_data["store"]
    settings = dict(flow["settings"])
    settings["enabled"] = bool(settings.get("enabled", True))
    try:
        if flow.get("kind") == "manual":
            saved = store.save_manual_rule(int(flow["owner_id"]), {**settings, "target_chat": flow["target_chat"]}, flow.get("rule_id"))
        elif flow.get("rule_id"):
            saved = store.update_rule(
                message.chat_id, int(flow["rule_id"]), str(flow["source_chat"]), str(flow["target_chat"]), settings
            )
        else:
            saved = store.add_rule(
                message.chat_id, str(flow["source_chat"]), str(flow["target_chat"]), settings
            )
    except (ValueError, sqlite3.IntegrityError) as exc:
        await message.reply_text(f"规则未保存：{exc}")
        return
    if not saved:
        await message.reply_text("这条来源和目标的规则已存在。请回主菜单编辑现有规则。")
        return
    context.user_data.pop("managed_flow", None)
    await message.reply_text("普通转发规则已保存。请在普通转发规则菜单中点击名称选择使用。" if flow.get("kind") == "manual" else "规则已保存并启用。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("普通转发规则", callback_data="menu:manual"), InlineKeyboardButton("主菜单", callback_data="menu:home")]]))


def _rule_summary(flow: dict[str, object]) -> str:
    settings = flow["settings"]
    source = "你主动发给机器人的消息（无需来源 ID）" if flow.get("kind") == "manual" else (flow.get("source_chat") or "未设置")
    target = flow.get("target_chat") or "未设置"
    media = "、".join(MEDIA_LABELS.get(item, str(item)) for item in settings.get("media_types", ["all"]))
    return (
        "请确认规则后保存：\n"
        f"名称：{settings.get('name') or '自动命名'}\n来源：{source}\n目标：{target}\n"
        f"读取方式：{'用户账号' if settings.get('source_mode') == 'user' else 'Bot API'}\n"
        f"发送账号：{'个人 Telegram 账号' if settings.get('sender_account') == 'user' else '机器人账号'}\n"
        f"消息类型：{media}\n包含词：{', '.join(settings.get('filter_keywords', [])) or '无'}\n"
        f"排除词：{', '.join(settings.get('exclude_keywords', [])) or '无'}\n"
        f"筛选范围：{'整个相册' if settings.get('filter_scope') == 'album' else '逐条消息'}\n"
        f"来源展示：{'保留来源' if settings.get('sender_mode') == 'forward' else '隐藏来源'}\n"
        f"说明文字：{CAPTION_LABELS.get(str(settings.get('caption_mode')), '保留原说明')}\n"
        f"删除词：{', '.join(settings.get('remove_keywords', [])) or '无'}\n"
        f"替换组：{len(settings.get('replacements', []))} 组\n"
        f"前缀/后缀：{'有' if settings.get('prefix_text') else '无'} / {'有' if settings.get('suffix_text') else '无'}\n"
        f"链接处理：{'移除' if settings.get('remove_links') else '保留'}\n"
        f"底部按钮：{settings.get('button_text') or '无'}\n"
        f"相册：{'合并' if settings.get('album_mode') == 'merge' else '逐条发送'}"
    )


async def _show_rules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message or update.callback_query.message
    rules = context.application.bot_data["store"].managed_rules(update.effective_user.id)
    if not rules:
        await message.reply_text("还没有转发规则。", reply_markup=_home_markup(context))
        return
    rows = []
    for rule in rules[:12]:
        label = str(rule["name"] or f"规则 {rule['id']}")[:28]
        rows.append([InlineKeyboardButton(f"{label} · {'启用' if rule['enabled'] else '停用'}", callback_data=f"menu:rule:{rule['id']}:edit")])
        rows.append([
            InlineKeyboardButton("启用" if not rule["enabled"] else "停用", callback_data=f"menu:rule:{rule['id']}:toggle"),
            InlineKeyboardButton("删除", callback_data=f"menu:rule:{rule['id']}:delete"),
        ])
    rows.append([InlineKeyboardButton("新建规则", callback_data="menu:new"), InlineKeyboardButton("返回", callback_data="menu:home")])
    await message.reply_text(f"转发规则（显示 {min(len(rules), 12)} / {len(rules)} 条）：", reply_markup=InlineKeyboardMarkup(rows))


async def _rule_action(update: Update, context: ContextTypes.DEFAULT_TYPE, parts: list[str]) -> None:
    query = update.callback_query
    if query is None or len(parts) != 3:
        return
    try:
        rule_id = int(parts[1])
    except ValueError:
        await query.answer("规则编号无效。", show_alert=True)
        return
    action = parts[2]
    store: Store = context.application.bot_data["store"]
    owner_id = update.effective_user.id
    rule = store.rule_for(owner_id, rule_id)
    if rule is None:
        await query.answer("找不到这条规则。", show_alert=True)
        return
    await query.answer()
    if action == "toggle":
        store.set_rule_enabled(owner_id, rule_id, not bool(rule["enabled"]))
        await query.message.reply_text(f"规则已{'启用' if not rule['enabled'] else '停用'}。")
        await _show_rules(update, context)
    elif action == "delete":
        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("确认删除", callback_data=f"menu:rule:{rule_id}:confirm"),
            InlineKeyboardButton("取消", callback_data="menu:rules"),
        ]])
        await query.message.reply_text(f"确认删除规则“{rule['name'] or rule_id}”？", reply_markup=markup)
    elif action == "confirm":
        store.delete_rule(owner_id, rule_id)
        await query.message.reply_text("规则已删除。")
        await _show_rules(update, context)
    elif action == "edit":
        await _begin_rule(update, context, rule)
    elif action == "list":
        await _show_rules(update, context)


async def _show_targets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message or update.callback_query.message
    targets = context.application.bot_data["store"].targets()
    if not targets:
        await message.reply_text("还没有目标频道或群组。", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("添加目标", callback_data="menu:target_add")],
            [InlineKeyboardButton("返回", callback_data="menu:home")],
        ]))
        return
    rows = [[InlineKeyboardButton(f"移除 {title[:40]}", callback_data=f"menu:target_remove:{chat_id}")] for chat_id, title in targets[:20]]
    rows.extend([
        [InlineKeyboardButton("添加目标", callback_data="menu:target_add")],
        [InlineKeyboardButton("返回", callback_data="menu:home")],
    ])
    await message.reply_text("已配置目标：\n" + "\n".join(f"• {title} ({chat_id})" for chat_id, title in targets[:20]), reply_markup=InlineKeyboardMarkup(rows))


async def _show_default_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message or update.callback_query.message
    store: Store = context.application.bot_data["store"]
    targets = store.targets()
    if not targets:
        await message.reply_text("还没有目标频道或群组，请先添加目标。", reply_markup=_home_markup(context))
        return
    current = store.default_target(update.effective_user.id)
    rows = [
        [InlineKeyboardButton(("✅ " if chat_id == current else "") + title[:50], callback_data=f"menu:default_set:{chat_id}")]
        for chat_id, title in targets[:50]
    ]
    if current:
        rows.append([InlineKeyboardButton("清除默认对象", callback_data="menu:default_clear")])
    rows.append([InlineKeyboardButton("返回主菜单", callback_data="menu:home")])
    await message.reply_text(
        "选择你自己的默认转发对象。设置后，你手动转发给机器人并选择发布账号后，会自动发送到该目标；其他用户仍需自行选择目标。",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def _remove_target(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: str) -> None:
    query = update.callback_query
    if query is None:
        return
    store: Store = context.application.bot_data["store"]
    if store.target_rule_count(chat_id):
        await query.answer("仍有规则使用该目标，请先删除或修改相关规则。", show_alert=True)
        return
    removed = store.remove_target(chat_id)
    await query.answer("已移除。" if removed else "目标不存在。")
    await _show_targets(update, context)
