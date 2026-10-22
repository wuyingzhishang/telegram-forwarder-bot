from __future__ import annotations

from urllib.parse import urlparse


def parse_message_link(value: str) -> tuple[str, int] | None:
    try:
        parsed = urlparse(value.strip())
        host = (parsed.hostname or "").casefold()
        if host not in {"t.me", "www.t.me", "telegram.me", "www.telegram.me"}:
            return None
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 2 or parts[0].startswith("+") or parts[0] == "joinchat":
            return None
        message_id = int(parts[-1])
        if message_id < 1:
            return None
        if parts[0] == "c":
            channel_id = int(parts[1])
            if channel_id < 1:
                return None
            return f"-100{channel_id}", message_id
        username = parts[0]
        if not username.replace("_", "").isalnum() or not username[0].isalpha():
            return None
        return f"@{username}", message_id
    except (ValueError, IndexError):
        return None
