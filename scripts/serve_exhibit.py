"""Serve an explicitly selected static exhibition folder using only Python stdlib."""

from __future__ import annotations

import argparse
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import sys
from urllib.parse import unquote, urlsplit
import webbrowser


DEFAULT_FILES = frozenset({
    "index.html", "tablet.html", "data.json", "source-records.csv", "README.txt",
    "manifest.json", "manifest.webmanifest", "sw.js", "icon.svg", "offline-guide.pdf",
    "app.js", "styles.css", "style.css", "favicon.ico",
})
ALLOWED_SUFFIXES = frozenset({
    ".html", ".json", ".webmanifest", ".js", ".css", ".csv", ".txt", ".pdf",
    ".svg", ".png", ".jpg", ".jpeg", ".webp", ".ico", ".woff", ".woff2",
})
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8", ".json": "application/json; charset=utf-8",
    ".webmanifest": "application/manifest+json; charset=utf-8",
    ".js": "application/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".csv": "text/csv; charset=utf-8", ".txt": "text/plain; charset=utf-8",
    ".pdf": "application/pdf", ".svg": "image/svg+xml", ".png": "image/png",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
    ".ico": "image/x-icon", ".woff": "font/woff", ".woff2": "font/woff2",
}
SECRET_NAMES = re.compile(r"(?:secret|credential|private[-_]?key|service[-_]?account|api[-_]?key|token)", re.I)


def valid_relative_name(name: str) -> bool:
    """Reject traversal, Windows paths, hidden files, and credential-like names."""
    if not isinstance(name, str) or not name or len(name) > 512:
        return False
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", name):
        return False
    parts = name.split("/")
    if any(not part or part in {".", ".."} or part.startswith(".") or SECRET_NAMES.search(part)
           for part in parts):
        return False
    return PurePosixPath(name).suffix.lower() in ALLOWED_SUFFIXES


def load_allowed_files(directory: Path) -> frozenset[str]:
    """An optional build-generated file list permits additional static assets."""
    manifest = directory / "exhibit-files.json"
    if not manifest.exists():
        return DEFAULT_FILES
    resolved = manifest.resolve(strict=True)
    if directory not in resolved.parents or not resolved.is_file() or resolved.stat().st_size > 65536:
        raise ValueError("The exhibit file list is not a valid local manifest.")
    try:
        value = json.loads(resolved.read_text(encoding="utf-8-sig"))
        names = value["files"]
    except (OSError, UnicodeError, ValueError, TypeError, KeyError):
        raise ValueError("exhibit-files.json must contain a JSON object with a files list.") from None
    if not isinstance(names, list) or not 1 <= len(names) <= 500 or any(not valid_relative_name(name) for name in names):
        raise ValueError("The exhibit file list contains unsupported or unsafe paths.")
    if "index.html" not in names:
        raise ValueError("The exhibit file list must include index.html.")
    return frozenset(names)


class ExhibitHandler(BaseHTTPRequestHandler):
    server_version = "BasilExhibit"
    sys_version = ""

    def __init__(self, *args, directory: Path, allowed_files: frozenset[str], **kwargs):
        self.directory = directory
        self.allowed_files = allowed_files
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):
        # Do not record request paths, queries, headers, or response contents.
        pass

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                         "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                         "font-src 'self' data:; connect-src 'self'; worker-src 'self'; "
                         "object-src 'none'; base-uri 'self'; frame-ancestors 'none'")
        super().end_headers()

    def _find_file(self):
        if len(self.path) > 2048:
            return None
        try:
            target = urlsplit(self.path)
            if target.scheme or target.netloc:
                return None
            decoded = unquote(target.path, errors="strict")
        except (ValueError, UnicodeError):
            return None
        if not decoded.startswith("/"):
            return None
        name = "index.html" if decoded == "/" else decoded[1:]
        if not valid_relative_name(name) or name not in self.allowed_files:
            return None
        try:
            resolved = self.directory.joinpath(*name.split("/")).resolve(strict=True)
            if self.directory not in resolved.parents or not resolved.is_file():
                return None
        except (OSError, ValueError, RuntimeError):
            return None
        return resolved

    def _serve(self, send_body):
        target = self._find_file()
        if target is None:
            self.send_error(404, "Exhibition file not found")
            return
        try:
            # Open the resolved path; the original request path is never used.
            with target.open("rb") as content:
                self.send_response(200)
                self.send_header("Content-Type", CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream"))
                self.send_header("Content-Length", str(target.stat().st_size))
                if target.name == "sw.js":
                    self.send_header("Service-Worker-Allowed", "/")
                self.end_headers()
                if send_body:
                    shutil.copyfileobj(content, self.wfile, length=64*1024)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except OSError:
            self.send_error(404, "Exhibition file not found")

    def do_GET(self):
        self._serve(True)

    def do_HEAD(self):
        self._serve(False)

    def _method_not_allowed(self):
        # Drain only a small declared body before closing, so Windows does not
        # reset the connection before the client can receive the 405 response.
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if 0 < length <= 65536:
                self.connection.settimeout(1)
                self.rfile.read(length)
        except (OSError, ValueError):
            pass
        self.close_connection = True
        self.send_response(405)
        self.send_header("Allow", "GET, HEAD")
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_POST = _method_not_allowed
    do_PUT = _method_not_allowed
    do_PATCH = _method_not_allowed
    do_DELETE = _method_not_allowed
    do_OPTIONS = _method_not_allowed
    do_TRACE = _method_not_allowed
    do_CONNECT = _method_not_allowed


def create_server(directory, host="127.0.0.1", port=8765):
    """The caller must name the folder explicitly; only loopback or LAN bind."""
    directory = Path(directory).expanduser().resolve(strict=True)
    if not directory.is_dir() or not (directory / "index.html").is_file():
        raise ValueError("Choose the static exhibition folder containing index.html.")
    if host not in {"127.0.0.1", "0.0.0.0"}:
        raise ValueError("Only loopback or explicit LAN binding is supported.")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("The port must be an integer between 0 and 65535.")
    allowed = load_allowed_files(directory)
    handler = partial(ExhibitHandler, directory=directory, allowed_files=allowed)
    return ThreadingHTTPServer((host, port), handler)


def lan_addresses():
    """Return actual IPv4 addresses reported for this computer; no probe traffic."""
    result = set()
    try:
        addresses = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM)
        for info in addresses:
            value = info[4][0]
            address = ipaddress.ip_address(value)
            if not (address.is_loopback or address.is_unspecified or address.is_multicast or address.is_link_local):
                result.add(value)
    except (OSError, ValueError):
        pass
    return sorted(result)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only local server for the Talking Basil exhibition package.")
    parser.add_argument("--directory", required=True, help="Static exhibition folder containing index.html")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--lan", action="store_true", help="Explicitly allow same-network tablet access")
    parser.add_argument("--open", action="store_true", help="Open the local page in a browser")
    args = parser.parse_args(argv)
    try:
        server = create_server(args.directory, "0.0.0.0" if args.lan else "127.0.0.1", args.port)
    except (OSError, ValueError, RuntimeError):
        print("Cannot start: check the exhibition folder and whether the port is already in use.", file=sys.stderr)
        return 1
    port = server.server_address[1]
    local_url = f"http://127.0.0.1:{port}/"
    print("\nTalking Basil Pot - exhibition viewer")
    print(f"Computer: {local_url}")
    if args.lan:
        addresses = lan_addresses()
        if addresses:
            for address in addresses:
                print(f"Same-Wi-Fi tablet: http://{address}:{port}/tablet.html")
        else:
            print("LAN address was not detected. Check this computer's IPv4 address in Windows network settings.")
        print("LAN mode exposes only the selected static exhibit files to the local network.")
    else:
        print("This computer only. Add --lan explicitly to enable same-Wi-Fi tablet access.")
    print("Keep this window open. Stop with Ctrl+C. No API key or package installation is required.\n")
    if args.open:
        webbrowser.open(local_url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nExhibition viewer stopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
