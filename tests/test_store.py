import sqlite3

from forwarder.store import Store


def test_rules_persist_and_route_by_chat_id_or_username(tmp_path):
    store = Store(str(tmp_path / "rules.sqlite3"))
    assert store.add_rule(10, "-100123", "@archive")
    assert not store.add_rule(10, "-100123", "@archive")
    assert store.rules_for(10) == [(1, "-100123", "@archive", True)]
    assert store.active_targets(-100123) == ["@archive"]
    assert store.active_targets("-100999") == []


def test_same_source_target_can_belong_to_different_owners(tmp_path):
    store = Store(str(tmp_path / "rules.sqlite3"))
    store.add_rule(10, "-1001", "-1002")
    store.add_rule(20, "-1001", "-1002")
    assert len(store.rules_for(10)) == 1
    assert len(store.rules_for(20)) == 1


def test_rejects_forwarding_cycles(tmp_path):
    store = Store(str(tmp_path / "rules.sqlite3"))
    store.add_rule(10, "-1001", "-1002")
    try:
        store.add_rule(10, "-1002", "-1001")
    except ValueError as exc:
        assert "loop" in str(exc)
    else:
        raise AssertionError("Expected a forwarding loop to be rejected")


def test_targets_are_persisted_and_removable(tmp_path):
    store = Store(str(tmp_path / "rules.sqlite3"))
    store.add_target(-100123, "Archive")
    store.add_target("-100456", "Review")
    assert store.targets() == [("-100123", "Archive"), ("-100456", "Review")]
    assert store.remove_target(-100123)
    assert store.targets() == [("-100456", "Review")]
    assert not store.remove_target(-100123)


def test_default_target_is_per_user_and_cleared_when_target_is_removed(tmp_path):
    store = Store(str(tmp_path / "defaults.sqlite3"))
    store.add_target("-100123", "Archive")
    store.add_target("-100456", "Review")

    store.set_default_target(7, "-100123")
    store.set_default_target(8, "-100456")
    assert store.default_target(7) == "-100123"
    assert store.default_target(8) == "-100456"

    store.remove_target("-100123")
    assert store.default_target(7) is None
    assert store.default_target(8) == "-100456"
    store.set_default_target(8, None)
    assert store.default_target(8) is None


def test_dashboard_heartbeat_and_activity_are_persisted(tmp_path):
    store = Store(str(tmp_path / "dashboard.sqlite3"))
    store.add_target("-100123", "Archive")

    assert store.heartbeat() is None
    store.set_heartbeat(1_800_000_000.0)
    assert store.heartbeat() == 1_800_000_000.0

    store.record_activity("-100123", "success", 3)
    store.record_activity("-100123", "failed")
    assert store.activity_summary() == {"success": 3, "failed": 1}
    assert len(store.daily_activity(7)) == 7
    assert store.recent_activity(1)[0]["status"] == "failed"
    assert store.recent_activity(2)[1]["target_title"] == "Archive"


def test_legacy_rules_are_migrated_with_compatible_defaults(tmp_path):
    database = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute(
            """CREATE TABLE rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                source_chat TEXT NOT NULL,
                target_chat TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                UNIQUE(owner_id, source_chat, target_chat)
            )"""
        )
        db.execute("INSERT INTO rules(owner_id, source_chat, target_chat) VALUES (7, '-1001', '-1002')")

    store = Store(str(database))
    rule = store.managed_rules(7)[0]
    assert rule["source_chat"] == "-1001"
    assert rule["target_chat"] == "-1002"
    assert rule["media_types"] == ["all"]
    assert rule["filter_keywords"] == []
    assert rule["caption_mode"] == "keep"
    assert rule["album_mode"] == "individual"
    assert rule["source_mode"] == "bot"
    assert rule["exclude_keywords"] == []
    assert rule["sender_mode"] == "copy"
    assert rule["remove_keywords"] == []
    assert rule["filter_scope"] == "message"


def test_rules_store_filters_caption_modes_album_mode_and_enabled_state(tmp_path):
    store = Store(str(tmp_path / "configured.sqlite3"))
    settings = {
        "name": "Pictures",
        "media_types": ["photo"],
        "filter_keywords": ["release", "2026"],
        "exclude_keywords": ["spoiler"],
        "caption_mode": "append",
        "caption_text": "Source: A",
        "album_mode": "individual",
        "sender_mode": "copy",
        "remove_keywords": ["Draft"],
        "replacements": [{"find": "Old", "replace": "New"}],
        "prefix_text": "Archive",
        "suffix_text": "Details",
        "remove_links": True,
        "button_text": "Open",
        "button_url": "https://example.com/archive",
        "filter_scope": "album",
        "source_mode": "user",
        "enabled": False,
    }
    assert store.add_rule(7, "-1001", "-1002", settings)
    rule = store.managed_rules(7)[0]
    assert rule["name"] == "Pictures"
    assert rule["media_types"] == ["photo"]
    assert rule["filter_keywords"] == ["release", "2026"]
    assert rule["exclude_keywords"] == ["spoiler"]
    assert rule["remove_keywords"] == ["Draft"]
    assert rule["replacements"] == [{"find": "Old", "replace": "New"}]
    assert rule["prefix_text"] == "Archive"
    assert rule["suffix_text"] == "Details"
    assert rule["remove_links"]
    assert rule["button_text"] == "Open"
    assert rule["button_url"] == "https://example.com/archive"
    assert rule["filter_scope"] == "album"
    assert rule["source_mode"] == "user"
    assert rule["caption_mode"] == "append"
    assert rule["caption_text"] == "Source: A"
    assert not rule["enabled"]
    assert store.active_rules("-1001") == []

    updated = {
        **settings,
        "enabled": True,
        "album_mode": "merge",
        "caption_mode": "keep",
        "caption_text": "",
    }
    assert store.update_rule(7, int(rule["id"]), "-1001", "-1002", updated)
    assert store.active_rules("-1001") == []
    assert store.active_rules("-1001", "user")[0]["album_mode"] == "merge"


def test_personal_sender_requires_user_source_and_is_persisted(tmp_path):
    store = Store(str(tmp_path / "personal-sender.sqlite3"))
    try:
        store.add_rule(7, "-1001", "-1002", {"sender_account": "user"})
    except ValueError as exc:
        assert "用户账号来源" in str(exc)
    else:
        raise AssertionError("Personal sending must require a user-session source")

    settings = {"source_mode": "user", "sender_account": "user", "media_types": ["photo"]}
    assert store.add_rule(7, "-1001", "-1002", settings)
    assert store.managed_rules(7)[0]["sender_account"] == "user"


def test_rule_update_rejects_cycles_and_dashboard_can_remove_rules(tmp_path):
    store = Store(str(tmp_path / "cycles.sqlite3"))
    store.add_rule(7, "-1001", "-1002")
    store.add_rule(7, "-1002", "-1003")
    rule_id = int(store.managed_rules(7)[1]["id"])
    try:
        store.update_rule(7, rule_id, "-1002", "-1001", {})
    except ValueError as exc:
        assert "loop" in str(exc)
    else:
        raise AssertionError("Expected an updated forwarding loop to be rejected")

    assert store.target_rule_count("-1002") == 1
    assert store.delete_rule(7, rule_id)
    assert not store.delete_rule(7, rule_id)


def test_user_session_tasks_can_be_queued_paused_and_resumed(tmp_path):
    store = Store(str(tmp_path / "user-tasks.sqlite3"))
    task_id = store.create_user_task(7, "history", "@source", item_limit=300)
    assert store.user_task(task_id)["status"] == "awaiting_target"
    assert store.set_user_task_target(task_id, 7, "-100200")

    task = store.claim_user_task()
    assert task["status"] == "running"
    store.update_user_task(task_id, cursor=123, progress=12)
    store.update_user_task(task_id, snapshot_start=100, snapshot_end=399)
    assert store.set_user_task_status(task_id, "paused")
    assert store.set_user_task_status(task_id, "queued")
    resumed = store.claim_user_task()
    assert resumed["cursor"] == 123
    assert resumed["progress"] == 12
    assert resumed["snapshot_start"] == 100
    assert resumed["snapshot_end"] == 399
    assert resumed["target_chat"] == "-100200"


def test_user_session_status_tracks_the_live_account(tmp_path):
    store = Store(str(tmp_path / "session-status.sqlite3"))

    assert store.user_session_account() is None
    store.set_user_session_account(42)
    assert store.user_session_account() == 42
    store.set_user_session_account(None)
    assert store.user_session_account() is None


def test_direct_tasks_upgrade_legacy_task_schema_and_preserve_existing_rows(tmp_path):
    database = tmp_path / "legacy-user-tasks.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute(
            """CREATE TABLE user_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('extract', 'history')),
                source_chat TEXT NOT NULL,
                target_chat TEXT NOT NULL DEFAULT '',
                source_message_id INTEGER,
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
            "INSERT INTO user_tasks(id, owner_id, kind, source_chat, item_limit) VALUES (1, 7, 'history', '@source', 300)"
        )

    store = Store(str(database))
    assert store.user_task(1)["kind"] == "history"
    assert store.user_task(1)["source_message_ids"] == []

    task_id = store.create_user_task(
        7, "direct", "@forwarder_bot", "-100200", source_message_ids=[50, 51], item_limit=2
    )
    direct = store.user_task(task_id)
    assert direct["kind"] == "direct"
    assert direct["source_message_ids"] == [50, 51]
    assert direct["status"] == "queued"
