"""Local HTTP checks against temporary exhibit folders; no external network."""

from contextlib import contextmanager
from http.client import HTTPConnection
import importlib.util
import json
from pathlib import Path
from threading import Thread

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "serve_exhibit.py"
spec = importlib.util.spec_from_file_location("exhibit_server", MODULE_PATH)
server_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server_module)


@pytest.fixture
def exhibition(tmp_path):
    folder = tmp_path / "exhibition"
    folder.mkdir()
    (folder / "index.html").write_text("<html><body>Saved basil evidence</body></html>", encoding="utf-8")
    (folder / "tablet.html").write_text("<html>Tablet evidence</html>", encoding="utf-8")
    (folder / "data.json").write_text('{"source":"historical"}', encoding="utf-8")
    (folder / "source-records.csv").write_text("Timestamp,Temperature\n2025-01-01,24\n", encoding="utf-8")
    (folder / "serve_exhibit.py").write_text("not downloadable", encoding="utf-8")
    (folder / "secrets.json").write_text('{"key":"must-not-be-returned"}', encoding="utf-8")
    return folder


@contextmanager
def running(folder):
    server = server_module.create_server(folder, port=0)
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def request(port, target, method="GET", body=None):
    connection = HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.request(method, target, body=body)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_static_get_and_head_return_correct_file_without_listing(exhibition):
    with running(exhibition) as port:
        status, headers, body = request(port, "/")
        assert status == 200 and b"Saved basil evidence" in body
        assert headers["Content-Type"].startswith("text/html")
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert "Python" not in headers["Server"]
        head_status, head_headers, head_body = request(port, "/", "HEAD")
        assert head_status == 200 and head_body == b""
        assert int(head_headers["Content-Length"]) == len(body)
        assert request(port, "/tablet.html")[0] == 200
        assert request(port, "/source-records.csv")[0] == 200


@pytest.mark.parametrize("target", [
    "/../secrets.json", "/%2e%2e/secrets.json", "/%252e%252e/secrets.json",
    "/..%5csecrets.json", "/%2f%2fsecrets.json", "/C:/Windows/system.ini",
    "/.streamlit/secrets.toml", "/secrets.json", "/serve_exhibit.py", "/start-exhibit.cmd",
    "/assets/", "/index.html/", "/index.html%00", "http://other.example/index.html",
])
def test_unsafe_or_unlisted_paths_cannot_read_files(exhibition, target):
    with running(exhibition) as port:
        status, _, body = request(port, target)
    assert status == 404
    assert b"must-not-be-returned" not in body
    assert b"not downloadable" not in body


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"])
def test_only_get_and_head_are_supported(exhibition, method):
    before = (exhibition / "index.html").read_bytes()
    with running(exhibition) as port:
        status, headers, body = request(port, "/index.html", method, b"replacement")
        assert status == 405
        assert headers["Allow"] == "GET, HEAD"
        assert body == b""
        assert request(port, "/")[2] == before
    assert (exhibition / "index.html").read_bytes() == before


def test_generated_allowlist_controls_extra_assets_and_denies_directory_listing(exhibition):
    assets = exhibition / "assets"
    assets.mkdir()
    (assets / "diagram.svg").write_text("<svg></svg>", encoding="utf-8")
    (assets / "unlisted.svg").write_text("<svg>private draft</svg>", encoding="utf-8")
    (exhibition / "exhibit-files.json").write_text(json.dumps({"files": ["index.html", "assets/diagram.svg"]}), encoding="utf-8")
    with running(exhibition) as port:
        assert request(port, "/assets/diagram.svg")[0] == 200
        assert request(port, "/assets/unlisted.svg")[0] == 404
        assert request(port, "/assets/")[0] == 404
        assert request(port, "/data.json")[0] == 404


@pytest.mark.parametrize("names", [
    ["index.html", "../outside.html"], ["index.html", ".env"], ["index.html", "secrets.json"],
    ["index.html", "assets/api-key.json"], ["index.html", "serve_exhibit.py"], ["data.json"],
])
def test_manifest_cannot_enable_private_paths_or_executable_files(exhibition, names):
    (exhibition / "exhibit-files.json").write_text(json.dumps({"files": names}), encoding="utf-8")
    with pytest.raises(ValueError):
        server_module.create_server(exhibition, port=0)


def test_symlink_outside_exhibit_is_rejected_even_if_listed(exhibition, tmp_path):
    outside = tmp_path / "outside.html"
    outside.write_text("outside-private-content", encoding="utf-8")
    try:
        (exhibition / "external.html").symlink_to(outside)
    except OSError:
        pytest.skip("This Windows account does not allow creating symbolic links.")
    (exhibition / "exhibit-files.json").write_text(json.dumps({"files": ["index.html", "external.html"]}), encoding="utf-8")
    with running(exhibition) as port:
        status, _, body = request(port, "/external.html")
    assert status == 404
    assert b"outside-private-content" not in body


def test_query_strings_are_not_paths_or_logged(exhibition, capsys):
    with running(exhibition) as port:
        assert request(port, "/index.html?token=do-not-log-this")[0] == 200
    captured = capsys.readouterr()
    assert "do-not-log-this" not in captured.out + captured.err


def test_cli_requires_explicit_directory():
    with pytest.raises(SystemExit) as error:
        server_module.main([])
    assert error.value.code == 2


def test_lan_address_inventory_excludes_loopback_and_link_local(monkeypatch):
    monkeypatch.setattr(server_module.socket, "getaddrinfo", lambda *args: [
        (2, 1, 6, "", ("127.0.0.1", 0)), (2, 1, 6, "", ("192.168.50.8", 0)),
        (2, 1, 6, "", ("10.20.30.4", 0)), (2, 1, 6, "", ("169.254.1.2", 0)),
    ])
    assert server_module.lan_addresses() == ["10.20.30.4", "192.168.50.8"]


@pytest.mark.parametrize("lan,host", [(False, "127.0.0.1"), (True, "0.0.0.0")])
def test_cli_binds_only_explicit_lan_and_does_not_auto_open(exhibition, monkeypatch, capsys, lan, host):
    seen = {}
    class DummyServer:
        server_address = (host, 8765)
        def serve_forever(self):
            raise KeyboardInterrupt
        def server_close(self):
            seen["closed"] = True

    def create(directory, bound_host, port):
        seen.update(directory=directory, host=bound_host, port=port)
        return DummyServer()

    monkeypatch.setattr(server_module, "create_server", create)
    monkeypatch.setattr(server_module, "lan_addresses", lambda: ["192.168.50.8"])
    monkeypatch.setattr(server_module.webbrowser, "open", lambda *_: pytest.fail("Unexpected browser launch"))
    args = ["--directory", str(exhibition)] + (["--lan"] if lan else [])
    assert server_module.main(args) == 0
    assert seen["host"] == host and seen["closed"]
    output = capsys.readouterr().out
    assert ("http://192.168.50.8:8765/tablet.html" in output) == lan


def test_explicit_open_flag_opens_only_loopback_url(exhibition, monkeypatch):
    opened = []
    class DummyServer:
        server_address = ("127.0.0.1", 8765)
        def serve_forever(self):
            raise KeyboardInterrupt
        def server_close(self):
            pass
    monkeypatch.setattr(server_module, "create_server", lambda *args: DummyServer())
    monkeypatch.setattr(server_module.webbrowser, "open", opened.append)
    assert server_module.main(["--directory", str(exhibition), "--open"]) == 0
    assert opened == ["http://127.0.0.1:8765/"]

def test_resolved_outside_path_is_rejected_on_platforms_without_symlink_privileges(exhibition, tmp_path, monkeypatch):
    outside = tmp_path / "external.html"
    outside.write_text("outside-private-content", encoding="utf-8")
    original_resolve = Path.resolve
    def resolved(path, *args, **kwargs):
        if path == exhibition / "external.html":
            return outside
        return original_resolve(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", resolved)
    handler = object.__new__(server_module.ExhibitHandler)
    handler.directory = exhibition
    handler.allowed_files = frozenset({"external.html"})
    handler.path = "/external.html"
    assert handler._find_file() is None

def test_windows_launcher_runs_from_extracted_folder_with_spaces(exhibition, tmp_path):
    import os
    import shutil
    import subprocess
    if os.name != "nt":
        pytest.skip("Windows batch launcher test.")
    if not (shutil.which("py") or shutil.which("python")):
        pytest.skip("No installed Python launcher on PATH.")
    package = tmp_path / "portable package"
    package.mkdir()
    shutil.copy2(MODULE_PATH, package / "serve_exhibit.py")
    shutil.copy2(MODULE_PATH.parents[1] / "start-exhibit.cmd", package / "start-exhibit.cmd")
    (package / "index.html").write_text("<html>portable</html>", encoding="utf-8")
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", str(package / "start-exhibit.cmd"), "--help"],
        cwd=package, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--directory" in result.stdout
    assert "--lan" in result.stdout
