from __future__ import annotations

import argparse
import asyncio
import getpass
import os
from pathlib import Path
import sys

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError


def _print_qr(url: str, output=None) -> None:
    try:
        import qrcode
    except ImportError as exc:
        raise RuntimeError("Install the user-session extra to render Telegram login QR codes.") from exc

    if output is None:
        output = sys.stdout
    code = qrcode.QRCode(border=4, box_size=1)
    code.add_data(url)
    code.make(fit=True)
    matrix = code.get_matrix()
    if len(matrix) % 2:
        matrix.append([False] * len(matrix[0]))

    print("请在手机 Telegram 中打开“设置 → 设备 → 连接桌面设备”并扫描下方二维码。", file=output)
    for index in range(0, len(matrix), 2):
        top, bottom = matrix[index], matrix[index + 1]
        line = []
        for upper, lower in zip(top, bottom):
            line.append("█" if upper and lower else "▀" if upper else "▄" if lower else " ")
        print("".join(line), file=output)
    print(file=output)


async def _login_with_qr(client: TelegramClient):
    qr_login = await client.qr_login()
    while True:
        print("请扫描临时登录二维码；二维码过期时会自动刷新。")
        _print_qr(qr_login.url)
        try:
            return await qr_login.wait()
        except asyncio.TimeoutError:
            await qr_login.recreate()
        except SessionPasswordNeededError:
            password = getpass.getpass("请输入 Telegram 两步验证密码：")
            await client.sign_in(password=password)
            return await client.get_me()


async def login(method: str = "qr") -> None:
    load_dotenv()
    api_id = os.getenv("API_ID", "").strip()
    api_hash = os.getenv("API_HASH", "").strip()
    if not api_id.isdigit() or not api_hash:
        raise RuntimeError("Set API_ID and API_HASH in .env before logging in.")
    session_path = Path(os.getenv("USER_SESSION_PATH", "data/user-session/forwarder"))
    session_path.parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(session_path), int(api_id), api_hash)
    try:
        if method == "phone":
            await client.start()
            account = await client.get_me()
        elif method == "qr":
            await client.connect()
            if await client.is_user_authorized():
                account = await client.get_me()
            else:
                account = await _login_with_qr(client)
        else:
            raise ValueError("Login method must be 'qr' or 'phone'.")
        await client.get_dialogs(limit=None)
        print(f"Authorized account id: {account.id}")
        print(f"Session file: {session_path}.session")
        print("Keep this session file private. Do not send it in chat or include it in backups you do not control.")
    finally:
        await client.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="Log in to the Telegram user account session.")
    parser.add_argument(
        "--method",
        choices=("qr", "phone"),
        default="qr",
        help="Login with a phone code instead of the default QR code.",
    )
    args = parser.parse_args()
    asyncio.run(login(args.method))


if __name__ == "__main__":
    main()
