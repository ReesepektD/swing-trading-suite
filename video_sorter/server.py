"""
Local HTTP server for the Stellar Sorter UI. Standard library only.

This server can move files, so it is locked down on three axes:

* **Binding** — loopback by default. Any other bind prints a loud warning.
* **Confinement** — every path from the client is resolved and must live under
  ``--root``. That kills ``../`` traversal and symlink escapes alike.
* **Token** — the page is served with a per-process random token that every API
  call must echo back in a header. Without it, any website the user happened to
  have open could POST to localhost and rearrange their library. Requests are
  also rejected if they carry a cross-origin ``Origin``.
"""

from __future__ import annotations

import json
import mimetypes
import secrets
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .metadata import VideoFile, ffprobe_path, probe
from .organizer import (
    ACTIONS, DEFAULT_RULE, MOVE, PROBE_DEPENDENT_RULES, RULES, apply_plan, plan_moves,
)
from .scanner import ProbeCache, scan
from .sorter import summarise

STATIC_DIR = Path(__file__).parent / "static"
MAX_BODY_BYTES = 8 * 1024 * 1024
DEFAULT_PORT = 8770


class SorterService:
    """Everything the request handler needs, minus the HTTP plumbing."""

    def __init__(self, root: Path, start_path: Path | None = None, use_cache: bool = True):
        self.root = Path(root).expanduser().resolve()
        self.start_path = Path(start_path).expanduser().resolve() if start_path else None
        self.token = secrets.token_urlsafe(24)
        self.cache = ProbeCache() if use_cache else None
        self._lock = threading.Lock()  # One mutating operation at a time.
        # Most recent scan, by path. Planning reuses it so probe data survives
        # the round trip without re-running ffprobe over the whole library.
        self._scanned: dict[str, VideoFile] = {}

    # -- path safety ------------------------------------------------------

    def resolve(self, raw: str) -> Path:
        """Resolve a client-supplied path, refusing anything outside the root.

        ``strict=False`` lets a not-yet-created destination resolve; symlinks
        in the existing prefix are still followed before the check, so a link
        pointing out of the root cannot be used to escape it.
        """
        if not raw or not str(raw).strip():
            raise ValueError("path is required")
        candidate = Path(str(raw)).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        candidate = candidate.resolve(strict=False)
        if candidate != self.root and self.root not in candidate.parents:
            raise PermissionError(f"path is outside the served root ({self.root})")
        return candidate

    # -- API handlers -----------------------------------------------------

    def health(self) -> dict:
        return {
            "root": str(self.root),
            "start_path": str(self.start_path) if self.start_path else None,
            "ffprobe": ffprobe_path() is not None,
            "default_rule": DEFAULT_RULE,
            "rules": [{"key": key, "label": label} for key, (_, label) in RULES.items()],
            "actions": list(ACTIONS),
        }

    def scan(self, body: dict) -> dict:
        root = self.resolve(body.get("path") or str(self.root))
        if not root.is_dir():
            raise ValueError(f"not a directory: {root}")
        files = scan(
            root,
            recursive=bool(body.get("recursive", True)),
            do_probe=bool(body.get("probe", True)),
            cache=self.cache,
            extensions=body.get("extensions") or None,
        )
        self._scanned = {str(f.path): f for f in files}
        return {
            "root": str(root),
            "files": [f.to_dict() for f in files],
            "summary": summarise(files),
        }

    def plan(self, body: dict):
        paths = body.get("paths") or []
        if not paths:
            raise ValueError("no files selected")
        destination = self.resolve(body.get("destination") or "")
        rule = body.get("rule") or DEFAULT_RULE
        action = body.get("action") or MOVE

        needs_probe = rule in PROBE_DEPENDENT_RULES
        videos = []
        for raw in paths:
            path = self.resolve(raw)
            if not path.is_file():
                continue  # Moved or deleted since the scan; drop it quietly.
            videos.append(self._load(path, needs_probe=needs_probe))
        if not videos:
            raise ValueError("none of the selected files still exist")

        return plan_moves(videos, destination, rule, action=action)

    def _load(self, path: Path, *, needs_probe: bool) -> VideoFile:
        """Rebuild a VideoFile, recovering its probe data from the cheapest source.

        Tried in order: the last scan, the on-disk probe cache, then ffprobe
        itself. The last step matters — without it a rule like "by resolution"
        would bucket unprobed files under "Unknown" instead of sorting them.
        """
        video = VideoFile.from_path(path)

        previous = self._scanned.get(str(path))
        if previous is not None and _unchanged(previous, video):
            return previous
        if self.cache and self.cache.get(video):
            return video
        if needs_probe:
            probe(video)
            if self.cache:
                self.cache.put(video)
                self.cache.save()
        return video

    def apply(self, body: dict) -> dict:
        # Re-planned server-side from the same inputs: a tampered client plan
        # cannot direct a write anywhere the rules would not have sent it.
        plan = self.plan(body)
        with self._lock:
            return apply_plan(plan, dry_run=False)

    def browse(self, raw: str | None) -> dict:
        target = self.resolve(raw) if raw else self.root
        if not target.is_dir():
            target = target.parent
        try:
            directories = sorted(
                (p for p in target.iterdir() if p.is_dir() and not p.name.startswith(".")),
                key=lambda p: p.name.lower(),
            )
        except PermissionError:
            directories = []
        parent = target.parent
        return {
            "path": str(target),
            "parent": str(parent) if target != self.root and parent != target else None,
            "directories": [{"name": p.name, "path": str(p)} for p in directories],
        }


def _unchanged(previous: VideoFile, current: VideoFile) -> bool:
    """True when the file on disk is the same one the earlier scan measured."""
    return (
        previous.probed
        and previous.size == current.size
        and int(previous.modified) == int(current.modified)
    )


class SorterHandler(BaseHTTPRequestHandler):
    server_version = "StellarSorter/1.0"
    protocol_version = "HTTP/1.1"
    service: SorterService  # injected via the server instance

    # -- routing ----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's naming
        route = urlparse(self.path)
        path = unquote(route.path)

        if path in ("/", "/index.html"):
            return self._send_index()
        if path.startswith("/static/"):
            return self._send_static(path[len("/static/"):])
        if not self._authorised():
            return
        if path == "/api/health":
            return self._send_json(self.service.health())
        if path == "/api/browse":
            query = parse_qs(route.query)
            return self._guarded(lambda: self.service.browse((query.get("path") or [None])[0]))
        self._send_error_json(HTTPStatus.NOT_FOUND, "no such endpoint")

    def do_POST(self) -> None:  # noqa: N802
        path = unquote(urlparse(self.path).path)
        if not self._authorised():
            return
        try:
            body = self._read_json()
        except ValueError as exc:
            return self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))

        if path == "/api/scan":
            return self._guarded(lambda: self.service.scan(body))
        if path == "/api/plan":
            return self._guarded(lambda: self.service.plan(body).to_dict())
        if path == "/api/apply":
            return self._guarded(lambda: self.service.apply(body))
        self._send_error_json(HTTPStatus.NOT_FOUND, "no such endpoint")

    # -- auth -------------------------------------------------------------

    def _authorised(self) -> bool:
        if self.headers.get("X-Sorter-Token") != self.service.token:
            self._send_error_json(HTTPStatus.FORBIDDEN, "bad or missing session token")
            return False
        # A cross-origin page can send a token-less request but never a forged
        # Origin, so this is belt-and-braces against a stale tab.
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != self.headers.get("Host"):
            self._send_error_json(HTTPStatus.FORBIDDEN, "cross-origin request refused")
            return False
        return True

    # -- responses --------------------------------------------------------

    def _guarded(self, produce) -> None:
        """Run a service call, mapping its exceptions onto status codes."""
        try:
            self._send_json(produce())
        except PermissionError as exc:
            self._send_error_json(HTTPStatus.FORBIDDEN, str(exc))
        except FileNotFoundError as exc:
            self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
        except (ValueError, NotADirectoryError) as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        except OSError as exc:
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def _send_index(self) -> None:
        try:
            html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        except OSError:
            return self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, "UI missing")
        html = html.replace("__TOKEN__", self.service.token)
        self._send_bytes(html.encode("utf-8"), "text/html; charset=utf-8", no_store=True)

    def _send_static(self, relative: str) -> None:
        target = (STATIC_DIR / relative).resolve()
        if STATIC_DIR.resolve() not in target.parents or not target.is_file():
            return self._send_error_json(HTTPStatus.NOT_FOUND, "not found")
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self._send_bytes(target.read_bytes(), content_type)

    def _send_json(self, payload) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self._send_bytes(body, "application/json; charset=utf-8", no_store=True)

    def _send_error_json(self, status: HTTPStatus, message: str) -> None:
        body = json.dumps({"error": message}).encode("utf-8")
        self._send_bytes(body, "application/json; charset=utf-8", status=status, no_store=True)

    def _send_bytes(self, body: bytes, content_type: str, status=HTTPStatus.OK, *, no_store=False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if no_store:
            self.send_header("Cache-Control", "no-store")
        # The UI is entirely first-party; no external origin needs to load it.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self' 'unsafe-inline'; frame-ancestors 'none'",
        )
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ValueError("invalid Content-Length")
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise ValueError("request body too large")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("expected a JSON object")
        return payload

    def log_message(self, fmt: str, *args) -> None:
        # Default logging writes a line per static asset; keep the console for
        # things the user actually needs to see.
        pass


def serve(
    root: Path | str,
    *,
    start_path: Path | str | None = None,
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
    use_cache: bool = True,
) -> None:
    """Run the UI server until interrupted."""
    service = SorterService(Path(root), Path(start_path) if start_path else None, use_cache)

    handler = type("BoundSorterHandler", (SorterHandler,), {"service": service})
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True

    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}/"
    print("  ✦ Stellar Sorter")
    print(f"    {url}")
    print(f"    root: {service.root}")
    if not ffprobe_path():
        print("    note: ffprobe not found — duration/resolution/codec unavailable")
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"    WARNING: bound to {host}, reachable beyond this machine.")
        print("             Anyone who can load the page can move files under the root.")
    print("    Ctrl-C to stop\n")

    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  ✦ shutting down")
    finally:
        httpd.server_close()
