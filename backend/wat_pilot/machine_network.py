"""Discover advertised LAN services and deliver qualified packages over SFTP."""
import base64
import hashlib
import hmac
import io
import ipaddress
import os
import posixpath
import socket
import stat
import sqlite3
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

import paramiko
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from pydantic import Field, field_validator
from zeroconf import IPVersion, ServiceBrowser, ServiceListener, Zeroconf

from .hardware import _profile
from .models import StrictModel

SCHEMA = """
CREATE TABLE IF NOT EXISTS machine_sftp (
    machine_id TEXT PRIMARY KEY, address TEXT NOT NULL, port INTEGER NOT NULL,
    username TEXT NOT NULL, folder TEXT NOT NULL, fingerprint TEXT NOT NULL,
    last_file TEXT, last_received TEXT, UNIQUE(address, port, username, folder)
);
"""
NETWORKS = tuple(map(ipaddress.ip_network, ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16")))


def lan_address(value):
    ip = ipaddress.IPv4Address(value)
    if not any(ip in net for net in NETWORKS):
        raise ValueError("请填写内网 IPv4 地址")
    return str(ip)


class Endpoint(StrictModel):
    address: str
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_.-]{0,63}$")
    folder: str = Field(min_length=1, max_length=512)
    fingerprint: str = Field(pattern=r"^SHA256:[A-Za-z0-9+/]{43}$")

    @field_validator("address")
    @classmethod
    def private_ip(cls, value):
        return lan_address(value)

    @field_validator("folder")
    @classmethod
    def absolute_folder(cls, value):
        if (not value.startswith("/") or ".." in value.split("/") or value.startswith("//")
                or any(ord(c) < 32 or c == "\\" for c in value)):
            raise ValueError("请填写机台上的绝对目录")
        return posixpath.normpath(value)


class Advertisements(ServiceListener):
    def __init__(self):
        self.names = set()
        self.lock = threading.Lock()

    def add_service(self, zc, type_, name):
        with self.lock:
            if len(self.names) < 512:
                self.names.add((type_, name))

    update_service = add_service

    def remove_service(self, zc, type_, name):
        with self.lock:
            self.names.discard((type_, name))


def discover():
    listener = Advertisements()
    results = {}
    with Zeroconf(ip_version=IPVersion.V4Only) as zc:
        browser = ServiceBrowser(zc, ["_sftp-ssh._tcp.local.", "_ssh._tcp.local."], listener=listener)
        try:
            threading.Event().wait(2)
            with listener.lock:
                names = sorted(listener.names)
            # The browser caches multicast announcements. Resolve missing records concurrently.
            with ThreadPoolExecutor(max_workers=24) as pool:
                infos = pool.map(lambda item: zc.get_service_info(*item, timeout=450), names)
                for info in infos:
                    if not info or not info.port:
                        continue
                    for address in info.parsed_addresses(IPVersion.V4Only):
                        try:
                            address = lan_address(address)
                        except ValueError:
                            continue
                        results[address, info.port] = {"address": address, "port": info.port,
                                                       "name": info.server.removesuffix(".local.")}
        finally:
            browser.cancel()
    return sorted(results.values(), key=lambda row: (row["name"], row["address"], row["port"]))


def machine_key(store, machine, create=False):
    directory = store.path.parent / "machine-keys"
    path = directory / str(UUID(str(machine)))
    if create and not path.exists():
        directory.mkdir(mode=0o700, exist_ok=True)
        private = rsa.generate_private_key(public_exponent=65537, key_size=3072).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption())
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as temp:
            temp.write(private)
        try:
            os.link(temp.name, path)
        except FileExistsError:
            pass
        finally:
            Path(temp.name).unlink()
    if not path.exists():
        raise HTTPException(409, "请先为这台机台生成连接公钥。")
    return paramiko.RSAKey.from_private_key(io.StringIO(path.read_text(encoding="ascii")))


@contextmanager
def connection(endpoint, key):
    try:
        with socket.create_connection((endpoint.address, endpoint.port), timeout=6) as sock:
            with paramiko.Transport(sock) as transport:
                transport.start_client(timeout=8)
                remote = transport.get_remote_server_key()
                digest = base64.b64encode(hashlib.sha256(remote.asbytes()).digest()).decode().rstrip("=")
                if not hmac.compare_digest("SHA256:" + digest, endpoint.fingerprint):
                    raise HTTPException(409, "机台 SSH 指纹不匹配。请在机台上核对主机密钥。")
                transport.auth_timeout = 10
                transport.auth_publickey(endpoint.username, key)
                # Paramiko waits for the subsystem acknowledgement before it exposes a channel.
                negotiation = threading.Timer(12, transport.close)
                negotiation.start()
                try:
                    client = paramiko.SFTPClient.from_transport(transport)
                finally:
                    negotiation.cancel()
                    negotiation.join()
                with client as sftp:
                    sftp.get_channel().settimeout(30)
                    yield sftp
    except paramiko.AuthenticationException as exc:
        raise HTTPException(409, "机台拒绝登录。请核对接收账号及已授权的公钥。") from exc
    except (OSError, EOFError, paramiko.SSHException) as exc:
        raise HTTPException(502, "无法完成 SFTP 操作。请检查机台连接、接收目录和账号权限。") from exc


def remote_matches(sftp, path, data):
    try:
        with sftp.open(path, "rb") as handle:
            digest = hashlib.sha256()
            left = len(data)
            while left:
                chunk = handle.read(min(left, 65536))
                if not chunk:
                    return False
                digest.update(chunk)
                left -= len(chunk)
            return not handle.read(1) and digest.digest() == hashlib.sha256(data).digest()
    except FileNotFoundError:
        return None


def verify_folder(sftp, folder):
    if not stat.S_ISDIR(sftp.stat(folder).st_mode or 0):
        raise HTTPException(409, "接收位置必须是已有目录。")
    probe = posixpath.join(folder, f".wat-{uuid4().hex}.check")
    content = os.urandom(32)
    created = False
    try:
        with sftp.open(probe, "wx") as handle:
            created = True
            handle.write(content)
        sftp.rename(probe, probe + ".ready")
        probe += ".ready"
        if not remote_matches(sftp, probe, content):
            raise HTTPException(409, "接收目录的回读校验失败。")
    finally:
        if created:
            sftp.remove(probe)


def deliver(store, machine, filename, data):
    with store.connect() as conn:
        row = conn.execute("SELECT * FROM machine_sftp WHERE machine_id=?", (machine,)).fetchone()
    if not row:
        raise HTTPException(409, "请先在机台档案中连接这台内网机台。")
    endpoint = Endpoint(**{k: row[k] for k in Endpoint.model_fields})
    digest = hashlib.sha256(data).hexdigest()
    filename = f"{filename[:-4]}-{digest[:16]}.zip"
    final = posixpath.join(endpoint.folder, filename)
    with connection(endpoint, machine_key(store, machine)) as sftp:
        exists = remote_matches(sftp, final, data)
        if exists is False:
            raise HTTPException(409, "接收目录中有同名但内容不同的文件。")
        if exists is None:
            temp = posixpath.join(endpoint.folder, f".wat-{uuid4().hex}.part")
            created = False
            try:
                with sftp.open(temp, "wx") as handle:
                    created = True
                    handle.write(data)
                if not remote_matches(sftp, temp, data):
                    raise HTTPException(502, "机台回读校验失败，请重新下发。")
                try:
                    sftp.rename(temp, final)
                    created = False
                except OSError:
                    if not remote_matches(sftp, final, data):
                        raise
            finally:
                if created:
                    sftp.remove(temp)
    with store.connect() as conn, conn:
        conn.execute("""UPDATE machine_sftp SET last_file=?, last_received=CURRENT_TIMESTAMP
                        WHERE machine_id=? AND address=? AND port=? AND username=? AND folder=? AND fingerprint=?""",
                     (filename, machine, endpoint.address, endpoint.port, endpoint.username, endpoint.folder, endpoint.fingerprint))
    return {"filename": filename, "sha256": digest, "path": final}


def install_network_routes(router, store):
    @router.get("/hardware/network")
    def connected():
        with store.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM machine_sftp ORDER BY machine_id")]

    @router.post("/hardware/network/discover")
    def scan():
        try:
            return discover()
        except OSError as exc:
            raise HTTPException(503, "无法发现内网设备，请检查网络连接，或按地址接入。") from exc

    @router.post("/hardware/machines/{machine}/sftp/key")
    def public_key(machine: UUID):
        with store.connect() as conn:
            _profile(conn, "machines", machine)
        key = machine_key(store, machine, create=True)
        return {"public_key": f"{key.get_name()} {key.get_base64()} wat-{machine}"}

    @router.put("/hardware/machines/{machine}/sftp")
    def connect(machine: UUID, endpoint: Endpoint):
        with store.connect() as conn:
            _profile(conn, "machines", machine)
        with connection(endpoint, machine_key(store, machine)) as sftp:
            verify_folder(sftp, endpoint.folder)
        try:
            with store.connect() as conn, conn:
                conn.execute("""INSERT INTO machine_sftp(machine_id,address,port,username,folder,fingerprint)
                    VALUES(?,?,?,?,?,?) ON CONFLICT(machine_id) DO UPDATE SET address=excluded.address,
                    port=excluded.port,username=excluded.username,folder=excluded.folder,fingerprint=excluded.fingerprint,
                    last_file=NULL,last_received=NULL WHERE
                    (address,port,username,folder,fingerprint) !=
                    (excluded.address,excluded.port,excluded.username,excluded.folder,excluded.fingerprint)""", (str(machine), endpoint.address, endpoint.port,
                    endpoint.username, endpoint.folder, endpoint.fingerprint))
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "这个接收目录已关联其他机台。") from exc
        return {"connected": True}

    @router.delete("/hardware/machines/{machine}/sftp")
    def disconnect(machine: UUID):
        with store.connect() as conn, conn:
            conn.execute("DELETE FROM machine_sftp WHERE machine_id=?", (str(machine),))
        return {"connected": False}
