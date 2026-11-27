from test_api import LOGIN, configured  # noqa: F401
from test_hardware import hardware_fixture


def test_machine_transport_needs_login_and_private_machine_address(configured, monkeypatch, tmp_path):
    monkeypatch.setenv("WAT_WORKBENCH_DB", str(tmp_path / "workbench.sqlite3"))
    with configured() as client:
        assert client.get("/api/hardware/network").status_code == 401
        client.post("/api/login", json=LOGIN)
        assert client.get("/api/hardware/network").json() == []
        _, _, machine, _ = hardware_fixture(client)
        url = f"/api/hardware/machines/{machine['id']}/sftp"
        first = client.post(url + "/key").json()["public_key"]
        assert first.startswith("ssh-rsa ")
        assert client.post(url + "/key").json()["public_key"] == first
        assert client.put(url, json={"address": "127.0.0.1", "port": 22, "username": "wat",
                                     "folder": "/incoming", "fingerprint": "SHA256:" + "A" * 43}).status_code == 422


def test_discover_pair_and_deliver_verified_recipe(configured, monkeypatch, tmp_path):
    import asyncio
    import hashlib
    import socket
    import threading
    from concurrent.futures import Future
    from contextlib import ExitStack

    import asyncssh
    from zeroconf import ServiceInfo, Zeroconf
    from test_recipe_authoring import native_fixture
    from wat_pilot import machine_network

    # Keep both services isolated on loopback while exercising the real LAN
    # address validation and all SSH/DNS-SD wire traffic.
    advertised = "192.168.23.82"
    real_connect = socket.create_connection
    def test_link(address, *args, **kwargs):
        if address[0] == advertised:
            address = ("127.0.0.1", address[1])
        return real_connect(address, *args, **kwargs)
    monkeypatch.setattr(socket, "create_connection", test_link)
    monkeypatch.setattr(machine_network, "Zeroconf", lambda **kw: Zeroconf(interfaces=["127.0.0.1"], **kw))
    monkeypatch.setenv("WAT_WORKBENCH_DB", str(tmp_path / "db"))
    inbox = tmp_path / "incoming"
    inbox.mkdir()
    with configured() as client, ExitStack() as cleanup:
        client.post("/api/login", json=LOGIN)
        plan, _, _, _, _, machine, _ = native_fixture(client, tmp_path)
        api = f"/api/hardware/machines/{machine['id']}/sftp"
        pem = client.post(api + "/key").json()["public_key"]
        authorized = asyncssh.import_public_key(pem)
        host_key = asyncssh.generate_private_key("ssh-ed25519")

        class Receiver(asyncssh.SSHServer):
            def begin_auth(self, username):
                return True

            def public_key_auth_supported(self):
                return True

            def validate_public_key(self, username, key):
                return username == "operator" and key == authorized

        class Inbox(asyncssh.SFTPServer):
            def open(self, path, pflags, attrs):
                if path.startswith(b"/readonly/") and pflags & asyncssh.FXF_WRITE:
                    raise asyncssh.SFTPPermissionDenied("read-only incoming directory")
                return super().open(path, pflags, attrs)

        loop = asyncio.new_event_loop()
        ready = Future()
        async def serve():
            try:
                server = await asyncssh.create_server(
                    Receiver, "127.0.0.1", 0, server_host_keys=[host_key],
                    sftp_factory=lambda channel: Inbox(channel, chroot=str(inbox)))
                ready.set_result(server)
            except Exception as error:
                ready.set_exception(error)

        def worker():
            asyncio.set_event_loop(loop)
            loop.run_until_complete(serve())
            loop.run_forever()
            loop.close()

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        cleanup.callback(thread.join, 5)
        cleanup.callback(loop.call_soon_threadsafe, loop.stop)
        server = ready.result(10)
        cleanup.callback(loop.call_soon_threadsafe, server.close)
        port = server.get_port()
        zc = cleanup.enter_context(Zeroconf(interfaces=["127.0.0.1"]))
        kind = "_sftp-ssh._tcp.local."
        service = ServiceInfo(kind, "4082-fixture." + kind, port=port, server="wat-peer.local.",
                              addresses=[socket.inet_aton(advertised)])
        zc.register_service(service)
        cleanup.callback(zc.unregister_service, service)
        found = client.post("/api/hardware/network/discover").json()
        assert {"address": advertised, "port": port, "name": "wat-peer"} in found
        settings = {"address": advertised, "port": port, "username": "operator", "folder": "/",
                    "fingerprint": host_key.get_fingerprint()}
        endpoint = f"/api/plans/{plan['id']}/recipe/lab"
        send = endpoint + "/deliver?revision=1"
        assert client.post(send).status_code == 409
        assert client.put(api, json={**settings, "fingerprint": "SHA256:" + "A" * 43}).status_code == 409
        assert client.put(api, json={**settings, "username": "other"}).status_code == 409
        readonly = inbox / "readonly"
        readonly.mkdir()
        assert client.put(api, json={**settings, "folder": "/readonly"}).status_code == 502
        assert client.get("/api/hardware/network").json() == []
        readonly.rmdir()
        assert not list(inbox.iterdir())
        assert client.put(api, json=settings).status_code == 200
        package = client.get(endpoint + "?revision=1").content
        response = client.post(send)
        assert response.status_code == 200, response.text
        receipt = response.json()
        path = inbox / receipt["filename"]
        assert path.read_bytes() == package
        assert receipt["sha256"] == hashlib.sha256(package).hexdigest()
        assert client.post(send).json() == receipt
        assert list(inbox.iterdir()) == [path]
        assert client.put(api, json=settings).status_code == 200
        assert client.get("/api/hardware/network").json()[0]["last_file"] == path.name
        assert client.post(endpoint + "/deliver?revision=2").status_code == 409
        path.write_bytes(b"existing unrelated bytes")
        assert client.post(send).status_code == 409
        assert path.read_bytes() == b"existing unrelated bytes"
        assert client.delete(api).status_code == 200
        assert client.post(send).status_code == 409
