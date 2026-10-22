from __future__ import annotations

import sqlite3
import json
import time
from urllib.parse import urlparse
from pathlib import Path


MEDIA_TYPES = {
    "all",
    "text",
    "photo",
    "video",
    "document",
    "audio",
    "animation",
    "voice",
    "video_note",
    "sticker",
}
CAPTION_MODES = {"keep", "remove", "replace", "prepend", "append"}
ALBUM_MODES = {"individual", "merge"}
SENDER_MODES = {"copy", "forward"}
FILTER_SCOPES = {"message", "album"}
SOURCE_MODES = {"bot", "user"}
USER_TASK_STATUSES = {"awaiting_target", "queued", "running", "paused", "completed", "failed", "cancelled"}


class Store:
    def __init__(self, database_path: str) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id INTEGER NOT NULL,
                    source_chat TEXT NOT NULL,
                    target_chat TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(owner_id, source_chat, target_chat)
                )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS targets (
                    chat_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL
                )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS user_preferences (
                    user_id INTEGER PRIMARY KEY,
                    default_target_chat_id TEXT NOT NULL
                )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS runtime_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS publishing_preferences (
                    user_id INTEGER PRIMARY KEY,
                    sender_account TEXT NOT NULL CHECK(sender_account IN ('bot', 'user')),
                    expires_at REAL NOT NULL
                )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS activity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    status TEXT NOT NULL CHECK (status IN ('success', 'failed')),
                    target_chat_id TEXT NOT NULL,
                    target_title TEXT NOT NULL,
                    item_count INTEGER NOT NULL DEFAULT 1
                )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS user_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id INTEGER NOT NULL,
                    kind TEXT NOT NULL CHECK (kind IN ('extract', 'history', 'direct')),
                    source_chat TEXT NOT NULL,
                    target_chat TEXT NOT NULL DEFAULT '',
                    source_message_id INTEGER,
                    source_message_ids TEXT NOT NULL DEFAULT '[]',
                    item_limit INTEGER NOT NULL DEFAULT 1,
                    snapshot_start INTEGER NOT NULL DEFAULT 0,
                    snapshot_end INTEGER NOT NULL DEFAULT 0,
                    cursor INTEGER NOT NULL DEFAULT 0,
                    progress INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'awaiting_target',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                )"""
            )
            task_columns = {str(row[1]) for row in db.execute("PRAGMA table_info(user_tasks)")}
            if "snapshot_start" not in task_columns:
                db.execute("ALTER TABLE user_tasks ADD COLUMN snapshot_start INTEGER NOT NULL DEFAULT 0")
            if "snapshot_end" not in task_columns:
                db.execute("ALTER TABLE user_tasks ADD COLUMN snapshot_end INTEGER NOT NULL DEFAULT 0")
            task_sql_row = db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='user_tasks'"
            ).fetchone()
            task_sql = str(task_sql_row[0] or "") if task_sql_row else ""
            if "'direct'" not in task_sql:
                db.execute(
                    """CREATE TABLE user_tasks_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        owner_id INTEGER NOT NULL,
                        kind TEXT NOT NULL CHECK (kind IN ('extract', 'history', 'direct')),
                        source_chat TEXT NOT NULL,
                        target_chat TEXT NOT NULL DEFAULT '',
                        source_message_id INTEGER,
                        source_message_ids TEXT NOT NULL DEFAULT '[]',
                        item_limit INTEGER NOT NULL DEFAULT 1,
                        snapshot_start INTEGER NOT NULL DEFAULT 0,
                        snapshot_end INTEGER NOT NULL DEFAULT 0,
                        cursor INTEGER NOT NULL DEFAULT 0,
                        progress INTEGER NOT NULL DEFAULT 0,
                        status TEXT NOT NULL DEFAULT 'awaiting_target',
                        error TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                    )"""
                )
                db.execute(
                    """INSERT INTO user_tasks_new(
                           id, owner_id, kind, source_chat, target_chat, source_message_id,
                           item_limit, snapshot_start, snapshot_end, cursor, progress, status,
                           error, created_at, updated_at
                       ) SELECT id, owner_id, kind, source_chat, target_chat, source_message_id,
                                item_limit, snapshot_start, snapshot_end, cursor, progress, status,
                                error, created_at, updated_at
                         FROM user_tasks"""
                )
                db.execute("DROP TABLE user_tasks")
                db.execute("ALTER TABLE user_tasks_new RENAME TO user_tasks")
            elif "source_message_ids" not in task_columns:
                db.execute("ALTER TABLE user_tasks ADD COLUMN source_message_ids TEXT NOT NULL DEFAULT '[]'")
            db.execute("CREATE INDEX IF NOT EXISTS user_tasks_queue_idx ON user_tasks(status, id)")
            if "rule_snapshot" not in {str(row[1]) for row in db.execute("PRAGMA table_info(user_tasks)")}:
                db.execute("ALTER TABLE user_tasks ADD COLUMN rule_snapshot TEXT NOT NULL DEFAULT '{}'")
            db.execute("""CREATE TABLE IF NOT EXISTS manual_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER NOT NULL,
                settings TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS manual_rule_selection (
                owner_id INTEGER PRIMARY KEY, rule_id INTEGER NOT NULL)""")
            rule_columns = {str(row[1]) for row in db.execute("PRAGMA table_info(rules)")}
            migrations = {
                "name": "TEXT NOT NULL DEFAULT ''",
                "media_types": "TEXT NOT NULL DEFAULT '[\"all\"]'",
                "filter_keywords": "TEXT NOT NULL DEFAULT '[]'",
                "caption_mode": "TEXT NOT NULL DEFAULT 'keep'",
                "caption_text": "TEXT NOT NULL DEFAULT ''",
                "album_mode": "TEXT NOT NULL DEFAULT 'individual'",
                "exclude_keywords": "TEXT NOT NULL DEFAULT '[]'",
                "sender_mode": "TEXT NOT NULL DEFAULT 'copy'",
                "remove_keywords": "TEXT NOT NULL DEFAULT '[]'",
                "replacements": "TEXT NOT NULL DEFAULT '[]'",
                "prefix_text": "TEXT NOT NULL DEFAULT ''",
                "suffix_text": "TEXT NOT NULL DEFAULT ''",
                "remove_links": "INTEGER NOT NULL DEFAULT 0",
                "button_text": "TEXT NOT NULL DEFAULT ''",
                "button_url": "TEXT NOT NULL DEFAULT ''",
                "filter_scope": "TEXT NOT NULL DEFAULT 'message'",
                "source_mode": "TEXT NOT NULL DEFAULT 'bot'",
            }
            for column, declaration in migrations.items():
                if column not in rule_columns:
                    db.execute(f"ALTER TABLE rules ADD COLUMN {column} {declaration}")
            if "sender_account" not in rule_columns:
                db.execute("ALTER TABLE rules ADD COLUMN sender_account TEXT NOT NULL DEFAULT 'bot'")
                db.execute("UPDATE rules SET sender_account='user' WHERE source_mode='user' AND sender_mode='forward'")
            db.execute("CREATE INDEX IF NOT EXISTS activity_created_at_idx ON activity(created_at)")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def manual_rules(self, owner_id: int) -> list[dict[str, object]]:
        with self._connect() as db:
            rows = db.execute("SELECT id, settings FROM manual_rules WHERE owner_id=? ORDER BY id DESC", (owner_id,)).fetchall()
        return [{**json.loads(raw), "id": rule_id, "owner_id": owner_id} for rule_id, raw in rows]

    def manual_rule(self, owner_id: int, rule_id: int) -> dict[str, object] | None:
        return next((rule for rule in self.manual_rules(owner_id) if rule["id"] == rule_id), None)

    def save_manual_rule(self, owner_id: int, settings: dict[str, object], rule_id: int | None = None) -> int:
        target = str(settings.get("target_chat", ""))
        account = str(settings.get("sender_account", "bot"))
        values = self._settings({**settings, "source_mode": account}, "手动提交", target)
        keys = ("name", "source_mode", "media_types", "filter_keywords", "caption_mode", "caption_text",
                "album_mode", "exclude_keywords", "sender_mode", "remove_keywords", "replacements",
                "prefix_text", "suffix_text", "remove_links", "button_text", "button_url", "filter_scope",
                "sender_account", "enabled")
        clean = dict(zip(keys, values))
        clean.pop("source_mode")
        for key in ("media_types", "filter_keywords", "exclude_keywords", "remove_keywords", "replacements"):
            clean[key] = json.loads(clean[key])
        for key in ("enabled", "remove_links"):
            clean[key] = bool(clean[key])
        clean["target_chat"] = target
        if clean["caption_mode"] in {"replace", "prepend", "append"} and not clean["caption_text"]:
            raise ValueError("请填写媒体说明文字。")
        with self._connect() as db:
            if not db.execute("SELECT 1 FROM targets WHERE chat_id=?", (target,)).fetchone():
                raise ValueError("请先保存目标频道。")
            raw = json.dumps(clean, ensure_ascii=False)
            if rule_id is not None:
                if not db.execute("UPDATE manual_rules SET settings=? WHERE id=? AND owner_id=?", (raw, rule_id, owner_id)).rowcount:
                    raise ValueError("普通转发规则不存在。")
            else:
                rule_id = int(db.execute("INSERT INTO manual_rules(owner_id, settings) VALUES (?, ?)", (owner_id, raw)).lastrowid)
        return rule_id

    def selected_manual_rule_id(self, owner_id: int) -> int | None:
        with self._connect() as db:
            row = db.execute("SELECT rule_id FROM manual_rule_selection WHERE owner_id=?", (owner_id,)).fetchone()
        return int(row[0]) if row else None

    def select_manual_rule(self, owner_id: int, rule_id: int | None) -> None:
        with self._connect() as db:
            if rule_id is None:
                db.execute("DELETE FROM manual_rule_selection WHERE owner_id=?", (owner_id,))
                return
            row = db.execute("SELECT settings FROM manual_rules WHERE owner_id=? AND id=?", (owner_id, rule_id)).fetchone()
            if not row or not json.loads(row[0])["enabled"]:
                raise ValueError("请选择自己的已启用普通转发规则。")
            db.execute("INSERT INTO manual_rule_selection VALUES (?, ?) ON CONFLICT(owner_id) DO UPDATE SET rule_id=excluded.rule_id", (owner_id, rule_id))

    def delete_manual_rule(self, owner_id: int, rule_id: int) -> bool:
        with self._connect() as db:
            # Keep a selected deleted ID as a tombstone: never silently revert to raw forwarding.
            return db.execute("DELETE FROM manual_rules WHERE owner_id=? AND id=?", (owner_id, rule_id)).rowcount > 0

    @staticmethod
    def _settings(settings: dict[str, object] | None, source: str, target: str) -> tuple[object, ...]:
        settings = settings or {}
        media_types = list(settings.get("media_types", ["all"]))
        keywords = [str(item).strip() for item in settings.get("filter_keywords", []) if str(item).strip()]
        exclude_keywords = [str(item).strip() for item in settings.get("exclude_keywords", []) if str(item).strip()]
        remove_keywords = [str(item).strip() for item in settings.get("remove_keywords", []) if str(item).strip()]
        replacements = settings.get("replacements", [])
        caption_mode = str(settings.get("caption_mode", "keep"))
        album_mode = str(settings.get("album_mode", "individual"))
        sender_mode = str(settings.get("sender_mode", "copy"))
        filter_scope = str(settings.get("filter_scope", "message"))
        source_mode = str(settings.get("source_mode", "bot"))
        sender_account = str(settings.get("sender_account", "bot"))
        if sender_account not in {"bot", "user"}:
            raise ValueError("Unsupported sender account.")
        if sender_account == "user" and source_mode != "user":
            raise ValueError("个人账号发送需要选择用户账号来源。")
        if sender_account == "user" and (settings.get("button_text") or settings.get("button_url")):
            raise ValueError("个人账号发送不支持机器人内联按钮。")
        if source_mode == "user" and sender_account == "bot" and sender_mode == "forward":
            raise ValueError("用户账号读取后由机器人发布需要选择隐藏来源复制。")
        button_text = str(settings.get("button_text", "")).strip()
        button_url = str(settings.get("button_url", "")).strip()
        if not media_types or any(item not in MEDIA_TYPES for item in media_types):
            raise ValueError("Unsupported media type.")
        if "all" in media_types and len(media_types) != 1:
            raise ValueError("The all media type cannot be combined with other types.")
        if caption_mode not in CAPTION_MODES:
            raise ValueError("Unsupported caption mode.")
        if album_mode not in ALBUM_MODES:
            raise ValueError("Unsupported album mode.")
        if sender_mode not in SENDER_MODES:
            raise ValueError("Unsupported sender mode.")
        if filter_scope not in FILTER_SCOPES:
            raise ValueError("Unsupported filter scope.")
        if source_mode not in SOURCE_MODES:
            raise ValueError("Unsupported source mode.")
        if not isinstance(replacements, list) or any(
            not isinstance(item, dict)
            or not str(item.get("find", "")).strip()
            or not isinstance(item.get("replace", ""), str)
            for item in replacements
        ):
            raise ValueError("Each replacement needs a non-empty find value and a text replacement.")
        clean_replacements = [
            {"find": str(item["find"]).strip(), "replace": str(item.get("replace", ""))}
            for item in replacements
        ]
        if button_text and not button_url:
            raise ValueError("Enter a URL for the message button.")
        if button_url and not button_text:
            raise ValueError("Enter button text for the message URL.")
        if button_url:
            parsed = urlparse(button_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("Message button URLs must use http:// or https://.")
        has_content_edits = any((
            str(settings.get("caption_mode", "keep")) != "keep",
            remove_keywords,
            clean_replacements,
            str(settings.get("prefix_text", "")),
            str(settings.get("suffix_text", "")),
            bool(settings.get("remove_links", False)),
            button_text,
        ))
        if sender_mode == "forward" and has_content_edits:
            raise ValueError("Hide-source copy mode is required for text edits and message buttons.")
        name = str(settings.get("name", "")).strip() or f"{source} → {target}"
        enabled = 1 if bool(settings.get("enabled", True)) else 0
        return (
            name,
            source_mode,
            json.dumps(media_types, ensure_ascii=False),
            json.dumps(keywords, ensure_ascii=False),
            caption_mode,
            str(settings.get("caption_text", "")),
            album_mode,
            json.dumps(exclude_keywords, ensure_ascii=False),
            sender_mode,
            json.dumps(remove_keywords, ensure_ascii=False),
            json.dumps(clean_replacements, ensure_ascii=False),
            str(settings.get("prefix_text", "")),
            str(settings.get("suffix_text", "")),
            1 if bool(settings.get("remove_links", False)) else 0,
            button_text,
            button_url,
            filter_scope,
            sender_account,
            enabled,
        )

    @staticmethod
    def _check_cycle(db: sqlite3.Connection, source: str, target: str, excluding_id: int | None = None) -> None:
        query = "SELECT source_chat, target_chat FROM rules WHERE enabled=1"
        params: tuple[object, ...] = ()
        if excluding_id is not None:
            query += " AND id != ?"
            params = (excluding_id,)
        graph: dict[str, set[str]] = {}
        for existing_source, existing_target in db.execute(query, params):
            graph.setdefault(str(existing_source), set()).add(str(existing_target))
        frontier = [target]
        visited: set[str] = set()
        while frontier:
            current = frontier.pop()
            if current == source:
                raise ValueError("This rule would create a forwarding loop.")
            if current in visited:
                continue
            visited.add(current)
            frontier.extend(graph.get(current, ()))

    def add_rule(
        self,
        owner_id: int,
        source: str,
        target: str,
        settings: dict[str, object] | None = None,
    ) -> bool:
        values = self._settings(settings, source, target)
        with self._connect() as db:
            if values[-1]:
                self._check_cycle(db, source, target)
            cursor = db.execute(
                """INSERT OR IGNORE INTO rules(
                    owner_id, source_chat, target_chat, name, source_mode, media_types, filter_keywords,
                    caption_mode, caption_text, album_mode, exclude_keywords, sender_mode,
                    remove_keywords, replacements, prefix_text, suffix_text, remove_links,
                    button_text, button_url, filter_scope, sender_account, enabled
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (owner_id, source, target, *values),
            )
            return cursor.rowcount > 0

    def update_rule(
        self,
        owner_id: int,
        rule_id: int,
        source: str,
        target: str,
        settings: dict[str, object],
    ) -> bool:
        values = self._settings(settings, source, target)
        with self._connect() as db:
            row = db.execute("SELECT id FROM rules WHERE id=? AND owner_id=?", (rule_id, owner_id)).fetchone()
            if row is None:
                return False
            if values[-1]:
                self._check_cycle(db, source, target, excluding_id=rule_id)
            cursor = db.execute(
                """UPDATE rules SET source_chat=?, target_chat=?, name=?, source_mode=?, media_types=?, filter_keywords=?,
                   caption_mode=?, caption_text=?, album_mode=?, exclude_keywords=?, sender_mode=?,
                   remove_keywords=?, replacements=?, prefix_text=?, suffix_text=?, remove_links=?,
                   button_text=?, button_url=?, filter_scope=?, sender_account=?, enabled=? WHERE id=? AND owner_id=?""",
                (source, target, *values, rule_id, owner_id),
            )
            return cursor.rowcount > 0

    def delete_rule(self, owner_id: int, rule_id: int) -> bool:
        with self._connect() as db:
            cursor = db.execute("DELETE FROM rules WHERE id=? AND owner_id=?", (rule_id, owner_id))
            return cursor.rowcount > 0

    def managed_rules(self, owner_id: int | None = None) -> list[dict[str, object]]:
        where = " WHERE owner_id=?" if owner_id is not None else ""
        params = (owner_id,) if owner_id is not None else ()
        with self._connect() as db:
            rows = db.execute(
                """SELECT id, owner_id, source_chat, target_chat, enabled, name, source_mode, media_types,
                          filter_keywords, caption_mode, caption_text, album_mode, exclude_keywords,
                          sender_mode, remove_keywords, replacements, prefix_text, suffix_text,
                          remove_links, button_text, button_url, filter_scope, sender_account
                   FROM rules""" + where + " ORDER BY id",
                params,
            ).fetchall()
        result = []
        for row in rows:
            result.append({
                "id": int(row[0]),
                "owner_id": int(row[1]),
                "source_chat": str(row[2]),
                "target_chat": str(row[3]),
                "enabled": bool(row[4]),
                "name": str(row[5]),
                "source_mode": str(row[6]),
                "media_types": self._decode_list(row[7], ["all"]),
                "filter_keywords": self._decode_list(row[8], []),
                "caption_mode": str(row[9]),
                "caption_text": str(row[10]),
                "album_mode": str(row[11]),
                "exclude_keywords": self._decode_list(row[12], []),
                "sender_mode": str(row[13]),
                "remove_keywords": self._decode_list(row[14], []),
                "replacements": self._decode_dict_list(row[15]),
                "prefix_text": str(row[16]),
                "suffix_text": str(row[17]),
                "remove_links": bool(row[18]),
                "button_text": str(row[19]),
                "button_url": str(row[20]),
                "filter_scope": str(row[21]),
                "sender_account": str(row[22]),
            })
        return result

    @staticmethod
    def _decode_dict_list(value: object) -> list[dict[str, str]]:
        try:
            decoded = json.loads(str(value))
        except (TypeError, ValueError):
            return []
        if not isinstance(decoded, list):
            return []
        return [
            {"find": str(item["find"]), "replace": str(item.get("replace", ""))}
            for item in decoded
            if isinstance(item, dict) and isinstance(item.get("find"), str)
        ]

    @staticmethod
    def _decode_list(value: object, fallback: list[str]) -> list[str]:
        try:
            decoded = json.loads(str(value))
        except (TypeError, ValueError):
            return fallback
        return decoded if isinstance(decoded, list) and all(isinstance(item, str) for item in decoded) else fallback

    def active_rules(self, source_chat: int | str, source_mode: str = "bot") -> list[dict[str, object]]:
        source = str(source_chat)
        return [
            rule for rule in self.managed_rules()
            if rule["source_chat"] == source and rule["enabled"] and rule["source_mode"] == source_mode
        ]

    def target_rule_count(self, chat_id: int | str) -> int:
        with self._connect() as db:
            row = db.execute("SELECT COUNT(*) FROM rules WHERE target_chat=?", (str(chat_id),)).fetchone()
            manual = sum(1 for (raw,) in db.execute("SELECT settings FROM manual_rules") if json.loads(raw).get("target_chat") == str(chat_id))
        return int(row[0]) + manual

    def rules_for(self, owner_id: int) -> list[tuple[int, str, str, bool]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, source_chat, target_chat, enabled FROM rules WHERE owner_id=? ORDER BY id",
                (owner_id,),
            ).fetchall()
        return [(int(i), str(s), str(t), bool(enabled)) for i, s, t, enabled in rows]

    def rule_for(self, owner_id: int, rule_id: int) -> dict[str, object] | None:
        return next((rule for rule in self.managed_rules(owner_id) if rule["id"] == rule_id), None)

    def set_rule_enabled(self, owner_id: int, rule_id: int, enabled: bool) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE rules SET enabled=? WHERE id=? AND owner_id=?",
                (1 if enabled else 0, rule_id, owner_id),
            )
        return cursor.rowcount > 0

    def create_user_task(
        self,
        owner_id: int,
        kind: str,
        source_chat: str,
        target_chat: str = "",
        source_message_id: int | None = None,
        item_limit: int = 1,
        source_message_ids: list[int] | None = None,
        rule_snapshot: dict[str, object] | None = None,
    ) -> int:
        if kind not in {"extract", "history", "direct"}:
            raise ValueError("Unsupported user-session task type.")
        message_ids = [int(message_id) for message_id in (source_message_ids or [])]
        if kind == "direct" and (not target_chat or not message_ids):
            raise ValueError("Direct forwarding tasks require a target and at least one message.")
        status = "queued" if target_chat else "awaiting_target"
        with self._connect() as db:
            cursor = db.execute(
                """INSERT INTO user_tasks(owner_id, kind, source_chat, target_chat, source_message_id,
                   source_message_ids, item_limit, status, rule_snapshot) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    owner_id, kind, source_chat, target_chat,
                    source_message_id if source_message_id is not None else (message_ids[0] if message_ids else None),
                    json.dumps(message_ids),
                    max(1, min(int(item_limit), 5000)), status, json.dumps(rule_snapshot or {}, ensure_ascii=False),
                ),
            )
            task_id = int(cursor.lastrowid)
        return task_id

    def user_task(self, task_id: int) -> dict[str, object] | None:
        with self._connect() as db:
            row = db.execute(
                """SELECT id, owner_id, kind, source_chat, target_chat, source_message_id,
                          item_limit, snapshot_start, snapshot_end, cursor, progress, status, error, created_at, updated_at,
                          source_message_ids, rule_snapshot
                   FROM user_tasks WHERE id=?""",
                (task_id,),
            ).fetchone()
        return self._user_task_dict(row) if row else None

    def user_tasks(self, owner_id: int | None = None, limit: int = 20) -> list[dict[str, object]]:
        limit = max(1, min(int(limit), 100))
        where = " WHERE owner_id=?" if owner_id is not None else ""
        params = (owner_id, limit) if owner_id is not None else (limit,)
        with self._connect() as db:
            rows = db.execute(
                """SELECT id, owner_id, kind, source_chat, target_chat, source_message_id,
                          item_limit, snapshot_start, snapshot_end, cursor, progress, status, error, created_at, updated_at,
                          source_message_ids, rule_snapshot
                   FROM user_tasks""" + where + " ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._user_task_dict(row) for row in rows]

    @staticmethod
    def _user_task_dict(row: tuple[object, ...]) -> dict[str, object]:
        return {
            "id": int(row[0]), "owner_id": int(row[1]), "kind": str(row[2]),
            "source_chat": str(row[3]), "target_chat": str(row[4]),
            "source_message_id": int(row[5]) if row[5] is not None else None,
            "item_limit": int(row[6]), "snapshot_start": int(row[7]), "snapshot_end": int(row[8]),
            "cursor": int(row[9]), "progress": int(row[10]), "status": str(row[11]),
            "error": str(row[12]), "created_at": str(row[13]), "updated_at": str(row[14]),
            "source_message_ids": [int(message_id) for message_id in json.loads(str(row[15] or "[]"))],
            "rule_snapshot": json.loads(str(row[16] or "{}")),
        }

    def set_user_task_target(self, task_id: int, owner_id: int, target_chat: str) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                """UPDATE user_tasks SET target_chat=?, status='queued', updated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                   WHERE id=? AND owner_id=? AND status='awaiting_target'""",
                (target_chat, task_id, owner_id),
            )
        return cursor.rowcount > 0

    def claim_user_task(self) -> dict[str, object] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT id FROM user_tasks WHERE status='queued' ORDER BY id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            task_id = int(row[0])
            cursor = db.execute(
                """UPDATE user_tasks SET status='running', updated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                   WHERE id=? AND status='queued'""",
                (task_id,),
            )
            if cursor.rowcount != 1:
                return None
        return self.user_task(task_id)

    def update_user_task(
        self,
        task_id: int,
        *,
        cursor: int | None = None,
        progress: int | None = None,
        snapshot_start: int | None = None,
        snapshot_end: int | None = None,
    ) -> None:
        sets = ["updated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"]
        values: list[object] = []
        if cursor is not None:
            sets.append("cursor=?")
            values.append(max(0, int(cursor)))
        if progress is not None:
            sets.append("progress=?")
            values.append(max(0, int(progress)))
        if snapshot_start is not None:
            sets.append("snapshot_start=?")
            values.append(max(0, int(snapshot_start)))
        if snapshot_end is not None:
            sets.append("snapshot_end=?")
            values.append(max(0, int(snapshot_end)))
        values.append(task_id)
        with self._connect() as db:
            db.execute(f"UPDATE user_tasks SET {', '.join(sets)} WHERE id=?", values)

    def set_user_task_status(self, task_id: int, status: str, error: str = "") -> bool:
        if status not in USER_TASK_STATUSES:
            raise ValueError("Unsupported user-session task status.")
        with self._connect() as db:
            cursor = db.execute(
                """UPDATE user_tasks SET status=?, error=?, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                   WHERE id=?""",
                (status, error[:500], task_id),
            )
        return cursor.rowcount > 0

    def active_targets(self, source_chat: int | str) -> list[str]:
        source = str(source_chat)
        with self._connect() as db:
            rows = db.execute(
                "SELECT DISTINCT target_chat FROM rules WHERE source_chat=? AND enabled=1",
                (source,),
            ).fetchall()
        return [str(row[0]) for row in rows]

    def add_target(self, chat_id: int | str, title: str) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                "INSERT OR REPLACE INTO targets(chat_id, title) VALUES (?, ?)",
                (str(chat_id), title),
            )
            return cursor.rowcount > 0

    def targets(self) -> list[tuple[str, str]]:
        with self._connect() as db:
            rows = db.execute("SELECT chat_id, title FROM targets ORDER BY title COLLATE NOCASE").fetchall()
        return [(str(chat_id), str(title)) for chat_id, title in rows]

    def default_target(self, user_id: int) -> str | None:
        with self._connect() as db:
            row = db.execute(
                """SELECT p.default_target_chat_id FROM user_preferences p
                   JOIN targets t ON t.chat_id=p.default_target_chat_id WHERE p.user_id=?""",
                (user_id,),
            ).fetchone()
        return str(row[0]) if row else None

    def publishing_preference(self, user_id: int) -> dict[str, object]:
        with self._connect() as db:
            row = db.execute(
                "SELECT sender_account, expires_at FROM publishing_preferences WHERE user_id=?",
                (user_id,),
            ).fetchone()
        expired = bool(row and float(row[1]) <= time.time())
        return {
            "sender_account": row[0] if row and not expired else None,
            "expires_at": float(row[1]) if row else None,
            "expired": expired,
        }

    def set_publishing_account(self, user_id: int, account: str | None) -> None:
        if account not in {None, "bot", "user"}:
            raise ValueError("Unknown publishing account.")
        with self._connect() as db:
            if account is None:
                db.execute("DELETE FROM publishing_preferences WHERE user_id=?", (user_id,))
            else:
                db.execute(
                    "INSERT INTO publishing_preferences VALUES (?, ?, ?) "
                    "ON CONFLICT(user_id) DO UPDATE SET sender_account=excluded.sender_account, expires_at=excluded.expires_at",
                    (user_id, account, time.time() + 30 * 24 * 60 * 60),
                )

    def set_default_target(self, user_id: int, chat_id: int | str | None) -> None:
        with self._connect() as db:
            if chat_id is None:
                db.execute("DELETE FROM user_preferences WHERE user_id=?", (user_id,))
                return
            target_id = str(chat_id)
            if db.execute("SELECT 1 FROM targets WHERE chat_id=?", (target_id,)).fetchone() is None:
                raise ValueError("Target does not exist.")
            db.execute(
                """INSERT INTO user_preferences(user_id, default_target_chat_id) VALUES (?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET default_target_chat_id=excluded.default_target_chat_id""",
                (user_id, target_id),
            )

    def remove_target(self, chat_id: int | str) -> bool:
        with self._connect() as db:
            db.execute("DELETE FROM user_preferences WHERE default_target_chat_id=?", (str(chat_id),))
            cursor = db.execute("DELETE FROM targets WHERE chat_id=?", (str(chat_id),))
            return cursor.rowcount > 0

    def set_heartbeat(self, timestamp: float) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO runtime_state(key, value) VALUES ('bot_heartbeat', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(timestamp),),
            )

    def heartbeat(self) -> float | None:
        with self._connect() as db:
            row = db.execute("SELECT value FROM runtime_state WHERE key='bot_heartbeat'").fetchone()
        return float(row[0]) if row else None

    def set_user_session_account(self, account_id: int | None) -> None:
        with self._connect() as db:
            if account_id is None:
                db.execute("DELETE FROM runtime_state WHERE key='user_session_account_id'")
                db.execute("DELETE FROM runtime_state WHERE key='user_session_heartbeat'")
            else:
                db.execute(
                    "INSERT INTO runtime_state(key, value) VALUES ('user_session_account_id', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(int(account_id)),),
                )
                db.execute(
                    "INSERT INTO runtime_state(key, value) VALUES ('user_session_heartbeat', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(time.time()),),
                )

    def heartbeat_user_session(self) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO runtime_state(key, value) VALUES ('user_session_heartbeat', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(time.time()),),
            )

    def user_session_account(self) -> int | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT a.value, h.value FROM runtime_state a JOIN runtime_state h "
                "ON h.key='user_session_heartbeat' WHERE a.key='user_session_account_id'"
            ).fetchone()
        try:
            if row is None or time.time() - float(row[1]) > 45:
                return None
            return int(row[0])
        except (TypeError, ValueError):
            return None

    def record_activity(self, target_chat_id: int | str, status: str, item_count: int = 1) -> None:
        if status not in {"success", "failed"}:
            raise ValueError("Activity status must be 'success' or 'failed'.")
        chat_id = str(target_chat_id)
        with self._connect() as db:
            row = db.execute("SELECT title FROM targets WHERE chat_id=?", (chat_id,)).fetchone()
            title = str(row[0]) if row else chat_id
            db.execute(
                "INSERT INTO activity(status, target_chat_id, target_title, item_count) VALUES (?, ?, ?, ?)",
                (status, chat_id, title, max(1, int(item_count))),
            )
            db.execute("DELETE FROM activity WHERE id <= (SELECT MAX(id) - 5000 FROM activity)")

    def activity_summary(self) -> dict[str, int]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT status, COALESCE(SUM(item_count), 0)
                   FROM activity
                   WHERE created_at >= strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-24 hours')
                   GROUP BY status"""
            ).fetchall()
        result = {"success": 0, "failed": 0}
        for status, count in rows:
            result[str(status)] = int(count)
        return result

    def daily_activity(self, days: int = 7) -> list[dict[str, int | str]]:
        days = max(1, min(int(days), 30))
        with self._connect() as db:
            rows = db.execute(
                """SELECT date(created_at) AS day, status, COALESCE(SUM(item_count), 0)
                   FROM activity
                   WHERE date(created_at) >= date('now', ?)
                   GROUP BY day, status
                   ORDER BY day""",
                (f"-{days - 1} days",),
            ).fetchall()
        totals: dict[str, dict[str, int]] = {}
        for day, status, count in rows:
            totals.setdefault(str(day), {"success": 0, "failed": 0})[str(status)] = int(count)
        with self._connect() as db:
            day_rows = db.execute(
                """WITH RECURSIVE days(day, n) AS (
                       SELECT date('now', ?), 1
                       UNION ALL SELECT date(day, '+1 day'), n + 1 FROM days WHERE n < ?
                   ) SELECT day FROM days""",
                (f"-{days - 1} days", days),
            ).fetchall()
        return [
            {"date": str(day), **totals.get(str(day), {"success": 0, "failed": 0})}
            for (day,) in day_rows
        ]

    def recent_activity(self, limit: int = 50) -> list[dict[str, int | str]]:
        limit = max(1, min(int(limit), 200))
        with self._connect() as db:
            rows = db.execute(
                """SELECT id, created_at, status, target_chat_id, target_title, item_count
                   FROM activity ORDER BY id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [
            {
                "id": int(row[0]),
                "created_at": str(row[1]),
                "status": str(row[2]),
                "target_chat_id": str(row[3]),
                "target_title": str(row[4]),
                "item_count": int(row[5]),
            }
            for row in rows
        ]
