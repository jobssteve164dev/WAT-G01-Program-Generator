import hashlib
import json
import os
import re
import secrets
import socket
import sys
import tempfile
import webbrowser
from getpass import getpass
from pathlib import Path


def read_password(prompt):
    if sys.stdin.isatty():
        return getpass(prompt)
    return input(prompt)


def account(path):
    if not path.exists():
        print("首次使用，请创建工作台管理员账号。")
        while True:
            name = input("管理员账号（以字母或数字开头）：").strip()
            if re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._@-]{0,79}", name):
                break
            print("可用 1 至 80 个英文字母、数字及 . _ @ -")
        while True:
            password = read_password("设置密码（输入时隐藏）：")
            if 1 <= len(password) <= 256 and password == read_password("再次输入密码："):
                break
            print("请输入相同的密码，长度为 1 至 256 个字符。")
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
            json.dump({"username": name, "hash": salt.hex() + ":" + digest.hex()}, handle)
        try:
            os.link(handle.name, path)
        except FileExistsError:
            pass
        finally:
            Path(handle.name).unlink()
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    import uvicorn

    root = Path(__file__).resolve().parent
    data = Path(os.environ["LOCALAPPDATA"]) / "WAT G01" / "data"
    data.mkdir(parents=True, exist_ok=True)
    port = int(os.getenv("WAT_PORT", "8080"))
    if not 1 <= port <= 65535:
        raise ValueError("端口必须在 1 至 65535 之间")
    origin = os.getenv("WAT_G0_ORIGIN", f"http://localhost:{port}")
    with socket.socket() as listener:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", port))
        credentials = account(data / "account.json")
        os.environ.update(WAT_G0_ADMIN_USERNAME=credentials["username"],
                          WAT_G0_ADMIN_HASH=credentials["hash"], WAT_G0_ORIGIN=origin,
                          WAT_FRONTEND_DIST=str(root / "static"),
                          WAT_WORKBENCH_DB=str(data / "workbench.sqlite3"))
        from wat_pilot.api import app
        from wat_pilot.auth import valid_origin

        if not valid_origin(origin):
            raise ValueError("请设置有效的访问地址")
        listener.listen()
        print(f"工作台地址 {origin}\n使用期间请保留此窗口，按 Ctrl+C 停止。", flush=True)
        webbrowser.open(origin)
        uvicorn.Server(uvicorn.Config(app, log_level="warning")).run(sockets=[listener])


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        print(f"工作台启动失败：{error}", file=sys.stderr)
        if sys.stdin.isatty():
            input("按回车关闭窗口。")
        sys.exit(1)
