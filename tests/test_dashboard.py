import asyncio
import base64

from httpx import ASGITransport, AsyncClient

from forwarder.dashboard import create_app
from forwarder.store import Store


def test_dashboard_requires_login_and_serves_redacted_overview(tmp_path, monkeypatch):
    database = tmp_path / "dashboard.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(database))
    monkeypatch.setenv("DASHBOARD_USERNAME", "operator")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-password")
    monkeypatch.setenv("ADMIN_IDS", "42")
    store = Store(str(database))
    store.add_target("-100123", "Archive")
    store.set_heartbeat(1_800_000_000.0)
    store.record_activity("-100123", "success", 2)

    auth = base64.b64encode(b"operator:test-password").decode()

    async def check_requests():
        async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
            assert (await client.get("/")).status_code == 401
            response = await client.get("/api/overview", headers={"Authorization": f"Basic {auth}"})
            assert response.status_code == 200
            body = response.json()
            assert body["metrics"]["success"] == 2
            assert body["targets"] == [{"chat_id": "-100123", "title": "Archive"}]
            assert body["activity"][0]["item_count"] == 2
            assert "test-password" not in response.text
            assert (await client.get("/healthz")).status_code == 200

    asyncio.run(check_requests())


def test_dashboard_manages_destinations_and_forwarding_rules(tmp_path, monkeypatch):
    database = tmp_path / "manage.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(database))
    monkeypatch.setenv("DASHBOARD_USERNAME", "operator")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-password")
    monkeypatch.setenv("ADMIN_IDS", "42")
    auth = base64.b64encode(b"operator:test-password").decode()
    headers = {"Authorization": f"Basic {auth}", "X-Relay-Request": "dashboard"}
    payload = {
        "name": "Release photos",
        "source_chat": "@public_source",
        "target_chat": "-100200",
        "enabled": True,
        "source_mode": "user",
        "sender_account": "bot",
        "media_types": ["photo"],
        "filter_keywords": ["release", "weekly"],
        "exclude_keywords": ["spoiler"],
        "filter_scope": "album",
        "sender_mode": "copy",
        "caption_mode": "append",
        "caption_text": "Archive",
        "album_mode": "individual",
        "remove_keywords": ["draft"],
        "replacements": [{"find": "Old", "replace": "New"}],
        "prefix_text": "Start",
        "suffix_text": "End",
        "remove_links": True,
        "button_text": "Open",
        "button_url": "https://example.com/archive",
    }

    async def check_management():
        async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
            assert (await client.post("/api/targets", json={"chat_id": "-100200"}, headers={"Authorization": headers["Authorization"]})).status_code == 403
            assert (await client.post("/api/targets", json={"chat_id": "-100200", "title": "Archive"}, headers=headers)).status_code == 201
            assert (await client.post("/api/rules", json=payload, headers=headers)).status_code == 201

            listed = await client.get("/api/rules", headers={"Authorization": headers["Authorization"]})
            assert listed.status_code == 200
            assert listed.json()[0]["target_title"] == "Archive"
            assert listed.json()[0]["source_mode"] == "user"
            assert listed.json()[0]["sender_account"] == "bot"
            assert listed.json()[0]["media_types"] == ["photo"]
            assert listed.json()[0]["filter_keywords"] == ["release", "weekly"]
            assert listed.json()[0]["exclude_keywords"] == ["spoiler"]
            assert listed.json()[0]["filter_scope"] == "album"
            assert listed.json()[0]["sender_mode"] == "copy"
            assert listed.json()[0]["remove_keywords"] == ["draft"]
            assert listed.json()[0]["replacements"] == [{"find": "Old", "replace": "New"}]
            assert listed.json()[0]["prefix_text"] == "Start"
            assert listed.json()[0]["suffix_text"] == "End"
            assert listed.json()[0]["remove_links"] is True
            assert listed.json()[0]["button_text"] == "Open"
            assert listed.json()[0]["button_url"] == "https://example.com/archive"

            assert (await client.delete("/api/targets/-100200", headers=headers)).status_code == 409
            rule_id = listed.json()[0]["id"]
            update = {**payload, "name": "Disabled archive", "enabled": False}
            changed = await client.put(f"/api/rules/{rule_id}", json=update, headers=headers)
            assert changed.status_code == 200
            assert changed.json()["name"] == "Disabled archive"
            assert not changed.json()["enabled"]

            personal = {
                **payload,
                "sender_account": "user",
                "caption_mode": "keep",
                "caption_text": "",
                "button_text": "",
                "button_url": "",
            }
            personal_result = await client.put(f"/api/rules/{rule_id}", json=personal, headers=headers)
            assert personal_result.status_code == 200
            assert personal_result.json()["sender_account"] == "user"

            invalid = {
                **payload,
                "source_chat": "-100101",
                "sender_mode": "forward",
                "caption_mode": "replace",
            }
            assert (await client.post("/api/rules", json=invalid, headers=headers)).status_code == 422
            assert (await client.delete(f"/api/rules/{rule_id}", headers=headers)).status_code == 204
            assert (await client.delete("/api/targets/-100200", headers=headers)).status_code == 204

    asyncio.run(check_management())
