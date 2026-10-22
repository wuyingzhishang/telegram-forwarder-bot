from __future__ import annotations

import os
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator

from .store import ALBUM_MODES, CAPTION_MODES, MEDIA_TYPES, Store

load_dotenv()

security = HTTPBasic(realm="Forwarder Dashboard")
WEB_DIR = Path(__file__).parent / "web"


class ProcessingPayload(BaseModel):
    name: str = Field(default="", max_length=80)
    target_chat: str = Field(min_length=2, max_length=64)
    enabled: bool = True
    sender_account: Literal["bot", "user"] = "bot"
    media_types: list[str] = Field(default_factory=lambda: ["all"], min_length=1, max_length=len(MEDIA_TYPES))
    filter_keywords: list[str] = Field(default_factory=list, max_length=50)
    exclude_keywords: list[str] = Field(default_factory=list, max_length=50)
    caption_mode: Literal["keep", "remove", "replace", "prepend", "append"] = "keep"
    caption_text: str = Field(default="", max_length=900)
    album_mode: Literal["individual", "merge"] = "individual"
    sender_mode: Literal["copy", "forward"] = "copy"
    filter_scope: Literal["message", "album"] = "message"
    remove_keywords: list[str] = Field(default_factory=list, max_length=50)
    replacements: list[dict[str, str]] = Field(default_factory=list, max_length=50)
    prefix_text: str = Field(default="", max_length=900)
    suffix_text: str = Field(default="", max_length=900)
    remove_links: bool = False
    button_text: str = Field(default="", max_length=64)
    button_url: str = Field(default="", max_length=2048)

    @field_validator(
        "target_chat", "name", "caption_text", "prefix_text", "suffix_text",
        "button_text", "button_url", mode="before"
    )
    @classmethod
    def trim_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("media_types")
    @classmethod
    def valid_media_types(cls, value: list[str]) -> list[str]:
        if any(item not in MEDIA_TYPES for item in value) or ("all" in value and len(value) != 1):
            raise ValueError("Invalid media type selection.")
        if len(set(value)) != len(value):
            raise ValueError("Media types must be unique.")
        return value

    @field_validator("filter_keywords", "exclude_keywords", "remove_keywords")
    @classmethod
    def clean_keywords(cls, value: list[str]) -> list[str]:
        keywords = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        if any(len(item) > 100 for item in keywords):
            raise ValueError("Keywords must be 100 characters or fewer.")
        return keywords

    @field_validator("replacements")
    @classmethod
    def clean_replacements(cls, value: list[dict[str, str]]) -> list[dict[str, str]]:
        replacements = []
        for item in value:
            find = item.get("find", "").strip()
            replacement = item.get("replace", "")
            if not find or not isinstance(replacement, str):
                raise ValueError("Each replacement needs a non-empty find value and a text replacement.")
            replacements.append({"find": find, "replace": replacement})
        return replacements

    @model_validator(mode="after")
    def compatible_options(self) -> "ProcessingPayload":
        if self.sender_account == "user" and (self.button_text or self.button_url):
            raise ValueError("Personal-account sending does not support bot inline buttons.")
        if self.caption_mode in {"replace", "prepend", "append"} and not self.caption_text:
            raise ValueError("Enter text for the selected caption mode.")
        if bool(self.button_text) != bool(self.button_url):
            raise ValueError("Enter both the message button text and URL.")
        if self.sender_mode == "forward" and any((
            self.caption_mode != "keep", self.remove_keywords, self.replacements,
            self.prefix_text, self.suffix_text, self.remove_links, self.button_text
        )):
            raise ValueError("Text edits and buttons require hide-source copy mode.")
        return self


class RulePayload(ProcessingPayload):
    source_chat: str = Field(min_length=2, max_length=40)
    source_mode: Literal["bot", "user"] = "bot"

    @model_validator(mode="after")
    def valid_source(self) -> "RulePayload":
        self.source_chat = self.source_chat.strip()
        if self.sender_account == "user" and self.source_mode != "user":
            raise ValueError("Personal-account sending requires a user-account source.")
        if self.source_mode == "bot" and not re.fullmatch(r"-?\d{1,20}", self.source_chat):
            raise ValueError("Bot mode sources must use a numeric Telegram chat ID.")
        if self.source_mode == "user" and not (
            re.fullmatch(r"-?\d{1,20}", self.source_chat)
            or re.fullmatch(r"@[A-Za-z0-9_]{5,32}", self.source_chat)
        ):
            raise ValueError("User mode sources must use a numeric chat ID or public @username.")
        return self


class TargetPayload(BaseModel):
    chat_id: str = Field(min_length=2, max_length=64)
    title: str = Field(default="", max_length=120)

    @field_validator("chat_id", "title", mode="before")
    @classmethod
    def trim_target_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("chat_id")
    @classmethod
    def valid_chat_id(cls, value: str) -> str:
        if not (re.fullmatch(r"-?\d{1,20}", value) or re.fullmatch(r"@[A-Za-z0-9_]{5,32}", value)):
            raise ValueError("Use a numeric chat ID or public @username.")
        return value


class PublishingPayload(BaseModel):
    sender_account: Literal["bot", "user"] | None = None


class DefaultTargetPayload(BaseModel):
    target_chat: str | None = None


class ManualSelectionPayload(BaseModel):
    rule_id: int | None = None


def create_app() -> FastAPI:
    app = FastAPI(title="Forwarder Dashboard", docs_url=None, redoc_url=None, openapi_url=None)
    store = Store(os.getenv("DATABASE_PATH", "data/forwarder.sqlite3"))

    async def require_login(credentials: HTTPBasicCredentials = Depends(security)) -> str:
        expected_user = os.getenv("DASHBOARD_USERNAME", "").strip()
        expected_password = os.getenv("DASHBOARD_PASSWORD", "")
        if not expected_user or not expected_password:
            raise HTTPException(status_code=503, detail="Dashboard login is not configured.")
        valid_user = secrets.compare_digest(credentials.username.encode(), expected_user.encode())
        valid_password = secrets.compare_digest(credentials.password.encode(), expected_password.encode())
        if not (valid_user and valid_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid dashboard credentials.",
                headers={"WWW-Authenticate": 'Basic realm="Forwarder Dashboard"'},
            )
        return credentials.username

    async def require_dashboard_request(
        request: Request,
        _: str = Depends(require_login),
    ) -> None:
        if request.headers.get("x-relay-request") != "dashboard":
            raise HTTPException(status_code=403, detail="Dashboard request header is required.")

    def dashboard_owner_id() -> int:
        admin_ids = [part.strip() for part in os.getenv("ADMIN_IDS", "").split(",") if part.strip()]
        if not admin_ids:
            raise HTTPException(status_code=503, detail="Dashboard rule ownership is not configured.")
        try:
            return int(admin_ids[0])
        except ValueError as exc:
            raise HTTPException(status_code=503, detail="Dashboard rule ownership is not configured.") from exc

    def check_target(target_chat: str) -> None:
        if target_chat not in {chat_id for chat_id, _ in store.targets()}:
            raise HTTPException(status_code=400, detail="Select a saved destination first.")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    async def index(_: str = Depends(require_login)) -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/api/overview")
    async def overview(_: str = Depends(require_login)) -> dict[str, object]:
        heartbeat = store.heartbeat()
        now = time.time()
        age = max(0, int(now - heartbeat)) if heartbeat is not None else None
        last_seen = datetime.fromtimestamp(heartbeat, timezone.utc).isoformat() if heartbeat is not None else None
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "bot": {
                "online": age is not None and age < 45,
                "last_seen_seconds": age,
                "last_seen_at": last_seen,
            },
            "metrics": {
                **store.activity_summary(),
                "target_count": len(store.targets()),
            },
            "daily": store.daily_activity(7),
            "targets": [
                {"chat_id": chat_id, "title": title}
                for chat_id, title in store.targets()
            ],
            "activity": store.recent_activity(100),
        }

    def publishing_settings() -> dict[str, object]:
        owner = dashboard_owner_id()
        enabled = os.getenv("USER_SESSION_ENABLED", "0").lower() in {"1", "true", "yes"}
        session = store.user_session_account() if enabled else None
        return {
            **store.publishing_preference(owner),
            "owner_id": owner,
            "default_target": store.default_target(owner),
            "personal_available": session == owner,
            "personal_status": ("ready" if session == owner else "mismatch" if session else "offline" if enabled else "disabled"),
        }

    @app.get("/api/publishing")
    async def publishing(_: str = Depends(require_login)) -> dict[str, object]:
        return publishing_settings()

    @app.put("/api/publishing/account")
    async def set_publishing(payload: PublishingPayload, _: None = Depends(require_dashboard_request)) -> dict[str, object]:
        if payload.sender_account == "user" and not publishing_settings()["personal_available"]:
            raise HTTPException(status_code=409, detail="个人账号未连接或与当前管理员不一致，请先完成同一账号的登录。")
        store.set_publishing_account(dashboard_owner_id(), payload.sender_account)
        return publishing_settings()

    @app.put("/api/publishing/target")
    async def set_publishing_target(payload: DefaultTargetPayload, _: None = Depends(require_dashboard_request)) -> dict[str, object]:
        if payload.target_chat is not None:
            check_target(payload.target_chat)
        store.set_default_target(dashboard_owner_id(), payload.target_chat)
        return publishing_settings()

    @app.get("/api/manual-rules")
    async def manual_rules(_: str = Depends(require_login)) -> dict[str, object]:
        owner = dashboard_owner_id()
        return {"rules": store.manual_rules(owner), "selected_id": store.selected_manual_rule_id(owner)}

    @app.put("/api/manual-selection")
    async def select_manual(payload: ManualSelectionPayload, _: None = Depends(require_dashboard_request)) -> dict[str, object]:
        try:
            store.select_manual_rule(dashboard_owner_id(), payload.rule_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"selected_id": payload.rule_id}

    @app.post("/api/manual-rules", status_code=201)
    async def add_manual(payload: ProcessingPayload, _: None = Depends(require_dashboard_request)) -> dict[str, object]:
        check_target(payload.target_chat)
        try:
            rule_id = store.save_manual_rule(dashboard_owner_id(), payload.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return store.manual_rule(dashboard_owner_id(), rule_id)

    @app.put("/api/manual-rules/{rule_id}")
    async def edit_manual(rule_id: int, payload: ProcessingPayload, _: None = Depends(require_dashboard_request)) -> dict[str, object]:
        owner = dashboard_owner_id()
        if store.manual_rule(owner, rule_id) is None:
            raise HTTPException(status_code=404, detail="普通转发规则不存在。")
        check_target(payload.target_chat)
        try:
            store.save_manual_rule(owner, payload.model_dump(), rule_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return store.manual_rule(owner, rule_id)

    @app.delete("/api/manual-rules/{rule_id}", status_code=204)
    async def delete_manual(rule_id: int, _: None = Depends(require_dashboard_request)) -> None:
        if not store.delete_manual_rule(dashboard_owner_id(), rule_id):
            raise HTTPException(status_code=404, detail="普通转发规则不存在。")

    @app.get("/api/targets")
    async def targets(_: str = Depends(require_login)) -> list[dict[str, str]]:
        return [{"chat_id": chat_id, "title": title} for chat_id, title in store.targets()]

    @app.post("/api/targets", status_code=status.HTTP_201_CREATED)
    async def add_target(
        payload: TargetPayload,
        _: None = Depends(require_dashboard_request),
    ) -> dict[str, str]:
        title = payload.title or payload.chat_id
        store.add_target(payload.chat_id, title)
        return {"chat_id": payload.chat_id, "title": title}

    @app.delete("/api/targets/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def remove_target(
        chat_id: str,
        _: None = Depends(require_dashboard_request),
    ) -> None:
        if store.target_rule_count(chat_id):
            raise HTTPException(status_code=409, detail="Remove or edit rules using this destination first.")
        if not store.remove_target(chat_id):
            raise HTTPException(status_code=404, detail="Destination was not found.")

    @app.get("/api/rules")
    async def rules(_: str = Depends(require_login)) -> list[dict[str, object]]:
        targets_by_id = {chat_id: title for chat_id, title in store.targets()}
        return [
            {**rule, "target_title": targets_by_id.get(str(rule["target_chat"]), str(rule["target_chat"]))}
            for rule in store.managed_rules(dashboard_owner_id())
        ]

    @app.post("/api/rules", status_code=status.HTTP_201_CREATED)
    async def add_rule(
        payload: RulePayload,
        _: None = Depends(require_dashboard_request),
    ) -> dict[str, object]:
        check_target(payload.target_chat)
        owner_id = dashboard_owner_id()
        settings = payload.model_dump(exclude={"source_chat", "target_chat"})
        try:
            inserted = store.add_rule(owner_id, payload.source_chat, payload.target_chat, settings)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not inserted:
            raise HTTPException(status_code=409, detail="A rule for this source and destination already exists.")
        return next(rule for rule in store.managed_rules(owner_id) if rule["source_chat"] == payload.source_chat and rule["target_chat"] == payload.target_chat)

    @app.put("/api/rules/{rule_id}")
    async def update_rule(
        rule_id: int,
        payload: RulePayload,
        _: None = Depends(require_dashboard_request),
    ) -> dict[str, object]:
        check_target(payload.target_chat)
        owner_id = dashboard_owner_id()
        settings = payload.model_dump(exclude={"source_chat", "target_chat"})
        try:
            updated = store.update_rule(owner_id, rule_id, payload.source_chat, payload.target_chat, settings)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not updated:
            raise HTTPException(status_code=404, detail="Rule was not found.")
        return next(rule for rule in store.managed_rules(owner_id) if rule["id"] == rule_id)

    @app.delete("/api/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def remove_rule(
        rule_id: int,
        _: None = Depends(require_dashboard_request),
    ) -> None:
        if not store.delete_rule(dashboard_owner_id(), rule_id):
            raise HTTPException(status_code=404, detail="Rule was not found.")

    app.mount("/assets", StaticFiles(directory=WEB_DIR / "assets"), name="assets")
    return app


app = create_app()
