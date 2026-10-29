#!/usr/bin/env python3
"""Serve the Girl Life site and authenticated cloud saves.

Tuned for many concurrent players: HTTP/1.1 keep-alive, one reused SQLite
connection per server thread, schema created once at start-up, constant-time
per-account usage accounting, and a hard cap on worker threads.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parent
DATABASE = Path(os.environ.get("GIRLLIFE_SAVE_DATABASE", "/var/lib/girl-life-saves/saves.sqlite3"))
MAX_REQUEST_BYTES = 6 * 1024 * 1024
MAX_PASSWORD_CHARACTERS = 1024
MAX_SAVE_BYTES = 4 * 1024 * 1024
MAX_ACCOUNT_SAVES = 512
MAX_ACCOUNT_STORAGE_BYTES = 256 * 1024 * 1024
SESSION_SECONDS = 7 * 24 * 60 * 60
MAX_CONCURRENT_REQUESTS = int(os.environ.get("GIRLLIFE_MAX_CONCURRENT_REQUESTS", "96"))
REQUEST_TIMEOUT_SECONDS = int(os.environ.get("GIRLLIFE_REQUEST_TIMEOUT", "65"))
LISTEN_PORT = int(os.environ.get("GIRLLIFE_PORT", "8081"))
GAME_ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
USERNAME = re.compile(r"[A-Za-z0-9_.-]{3,32}\Z")

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    salt BLOB NOT NULL,
    password_hash BLOB NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash BLOB PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at);
CREATE TABLE IF NOT EXISTS saves (
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    game_id TEXT NOT NULL,
    slot INTEGER NOT NULL,
    save_key TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    data BLOB NOT NULL,
    PRIMARY KEY (account_id, game_id, slot, save_key)
);
CREATE INDEX IF NOT EXISTS saves_by_time ON saves(account_id, game_id, timestamp DESC);
CREATE TABLE IF NOT EXISTS account_usage (
    account_id INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    saves INTEGER NOT NULL DEFAULT 0,
    bytes INTEGER NOT NULL DEFAULT 0
);
CREATE TRIGGER IF NOT EXISTS saves_usage_insert AFTER INSERT ON saves BEGIN
    INSERT INTO account_usage(account_id, saves, bytes) VALUES (NEW.account_id, 1, length(NEW.data))
    ON CONFLICT(account_id) DO UPDATE SET saves = saves + 1, bytes = bytes + length(NEW.data);
END;
CREATE TRIGGER IF NOT EXISTS saves_usage_update AFTER UPDATE OF data ON saves BEGIN
    UPDATE account_usage SET bytes = bytes + length(NEW.data) - length(OLD.data)
    WHERE account_id = OLD.account_id;
END;
CREATE TRIGGER IF NOT EXISTS saves_usage_delete AFTER DELETE ON saves BEGIN
    UPDATE account_usage SET saves = max(saves - 1, 0), bytes = max(bytes - length(OLD.data), 0)
    WHERE account_id = OLD.account_id;
END;
"""

_schema_lock = threading.Lock()
_schema_ready = False
_thread_state = threading.local()


def initialize_database() -> None:
    """Create the schema once per process instead of on every request."""
    global _schema_ready
    with _schema_lock:
        if _schema_ready:
            return
        DATABASE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        connection = sqlite3.connect(DATABASE, timeout=10)
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.executescript(SCHEMA)
            connection.execute(
                "INSERT INTO account_usage(account_id, saves, bytes) "
                "SELECT account_id, count(*), coalesce(sum(length(data)), 0) FROM saves "
                "WHERE account_id NOT IN (SELECT account_id FROM account_usage) GROUP BY account_id"
            )
            connection.commit()
        finally:
            connection.close()
        _schema_ready = True


def thread_connection() -> sqlite3.Connection:
    """Reuse one SQLite connection per worker thread (WAL allows parallel readers)."""
    connection = getattr(_thread_state, "connection", None)
    if connection is None:
        connection = sqlite3.connect(DATABASE, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA cache_size = -16384")
        _thread_state.connection = connection
    return connection


@contextmanager
def database():
    initialize_database()
    connection = thread_connection()
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def password_hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)


class GirlLifeHandler(SimpleHTTPRequestHandler):
    server_version = "GirlLife/1.1"
    protocol_version = "HTTP/1.1"
    timeout = REQUEST_TIMEOUT_SECONDS

    def __init__(self, *args, **kwargs):
        self._body_consumed = False
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self) -> None:
        if self.close_connection and not getattr(self, "_close_header_sent", False):
            # The stdlib never writes this header itself; without it a keep-alive
            # client would try to reuse a socket we are about to close.
            self._close_header_sent = True
            self.send_header("Connection", "close")
        if getattr(self, "_serve_qsp_gzip", False):
            self.send_header("Content-Encoding", "gzip")
            original_size = getattr(self, "_original_qsp_size", None)
            if original_size is not None:
                self.send_header("X-Original-Content-Length", str(original_size))
        origin = self.headers.get("Origin", "")
        if origin == "https://girllife.mewcat.me" or re.fullmatch(
            r"https?://(?:localhost|127\.0\.0\.1)(?::\d+)?", origin
        ):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        super().end_headers()

    def send_head(self):
        self._serve_qsp_gzip = False
        route = urlsplit(self.path)
        if route.path.lower().endswith(".qsp") and accepts_gzip(self.headers.get("Accept-Encoding", "")):
            original_path = self.path
            compressed_path = route.path + ".gz"
            original_file = Path(self.translate_path(route.path))
            if original_file.is_file() and Path(self.translate_path(compressed_path)).is_file():
                self._original_qsp_size = original_file.stat().st_size
                self.path = compressed_path + (f"?{route.query}" if route.query else "")
                self._serve_qsp_gzip = True
                try:
                    return super().send_head()
                finally:
                    self.path = original_path
        return super().send_head()

    def guess_type(self, path: str) -> str:
        if getattr(self, "_serve_qsp_gzip", False):
            return "application/octet-stream"
        return super().guess_type(path)

    def log_message(self, format: str, *args) -> None:
        # Static asset hits are the bulk of the traffic; keep the journal for the API and failures.
        code = str(args[1]) if len(args) > 1 else ""
        if urlsplit(self.path).path.startswith("/api/") or not code.startswith("2"):
            super().log_message(format, *args)

    def do_OPTIONS(self) -> None:
        if urlsplit(self.path).path.startswith("/api/"):
            self.send_response(204)
            self._close_if_body_unread()
            self.end_headers()
        else:
            self.send_error(404)

    def do_GET(self) -> None:
        if urlsplit(self.path).path.startswith("/api/"):
            self.handle_api("GET")
        else:
            super().do_GET()

    def do_POST(self) -> None:
        self.handle_api("POST")

    def do_PUT(self) -> None:
        self.handle_api("PUT")

    def do_DELETE(self) -> None:
        self.handle_api("DELETE")

    def handle_api(self, method: str) -> None:
        route = urlsplit(self.path)
        if route.path not in {"/api/accounts", "/api/sessions", "/api/saves"}:
            self.send_json(404, {"error": "Not found"})
            return
        try:
            if route.path == "/api/accounts" and method == "POST":
                self.create_account()
            elif route.path == "/api/sessions" and method == "POST":
                self.create_session()
            elif route.path == "/api/saves":
                self.handle_saves(method, parse_qs(route.query))
            else:
                self.send_json(405, {"error": "Method not allowed"})
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            self.send_json(400, {"error": str(error) or "Invalid request"})
        except sqlite3.Error:
            self.send_json(503, {"error": "Save storage is temporarily unavailable"})

    def read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("Invalid content length") from error
        if length < 1 or length > MAX_REQUEST_BYTES:
            raise ValueError("Request is empty or too large")
        value = json.loads(self.rfile.read(length))
        self._body_consumed = True
        if not isinstance(value, dict):
            raise ValueError("Expected a JSON object")
        return value

    def create_account(self) -> None:
        value = self.read_json()
        username = value.get("username", "")
        password = value.get("password", "")
        if not isinstance(username, str) or not USERNAME.fullmatch(username):
            self.send_json(400, {"error": "Account names must be 3-32 letters, numbers, dots, dashes, or underscores"})
            return
        if not isinstance(password, str) or not password:
            self.send_json(400, {"error": "Password is required"})
            return
        if len(password) > MAX_PASSWORD_CHARACTERS:
            self.send_json(400, {"error": "Password must be 1024 characters or fewer"})
            return
        salt = secrets.token_bytes(16)
        with database() as connection:
            try:
                cursor = connection.execute(
                    "INSERT INTO accounts(username, salt, password_hash, created_at) VALUES (?, ?, ?, ?)",
                    (username.lower(), salt, password_hash(password, salt), int(time.time())),
                )
            except sqlite3.IntegrityError:
                self.send_json(409, {"error": "Account already exists; sign in instead"})
                return
            self.issue_session(connection, cursor.lastrowid, username)

    def create_session(self) -> None:
        value = self.read_json()
        username = value.get("username", "")
        password = value.get("password", "")
        if not isinstance(username, str) or not isinstance(password, str):
            self.send_json(401, {"error": "Invalid account or password"})
            return
        with database() as connection:
            account = connection.execute(
                "SELECT id, username, salt, password_hash FROM accounts WHERE username = ? COLLATE NOCASE",
                (username.lower(),),
            ).fetchone()
            if account is None or not hmac.compare_digest(
                password_hash(password, account["salt"]), account["password_hash"]
            ):
                self.send_json(401, {"error": "Invalid account or password"})
                return
            self.issue_session(connection, account["id"], account["username"])

    def issue_session(self, connection: sqlite3.Connection, account_id: int, username: str) -> None:
        token = secrets.token_urlsafe(32)
        connection.execute("DELETE FROM sessions WHERE expires_at < ?", (int(time.time()),))
        connection.execute(
            "INSERT INTO sessions(token_hash, account_id, expires_at) VALUES (?, ?, ?)",
            (hashlib.sha256(token.encode()).digest(), account_id, int(time.time()) + SESSION_SECONDS),
        )
        connection.commit()
        self.send_json(201, {"token": token, "username": username})

    def authenticated_account(self) -> int | None:
        scheme, _, token = self.headers.get("Authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            return None
        with database() as connection:
            row = connection.execute(
                "SELECT account_id FROM sessions WHERE token_hash = ? AND expires_at >= ?",
                (hashlib.sha256(token.encode()).digest(), int(time.time())),
            ).fetchone()
        return row["account_id"] if row else None

    def handle_saves(self, method: str, query: dict[str, list[str]]) -> None:
        account_id = self.authenticated_account()
        if account_id is None:
            self.send_json(401, {"error": "Sign in to access cloud saves"})
            return
        game_id = query.get("game_id", [""])[0]
        if not GAME_ID.fullmatch(game_id):
            self.send_json(400, {"error": "Invalid game ID"})
            return
        slot_text = query.get("slot", [""])[0]
        save_key = query.get("key", [""])[0]
        if bool(slot_text) == bool(save_key):
            if method != "GET" or slot_text or save_key:
                self.send_json(400, {"error": "Choose either a slot or a save key"})
                return
            with database() as connection:
                rows = connection.execute(
                    "SELECT slot, save_key, timestamp FROM saves WHERE account_id = ? AND game_id = ? ORDER BY timestamp DESC",
                    (account_id, game_id),
                ).fetchall()
            self.send_json(200, [{"slot": row["slot"], "key": row["save_key"], "timestamp": row["timestamp"]} for row in rows])
            return
        try:
            slot = int(slot_text) if slot_text else -1
        except ValueError:
            self.send_json(400, {"error": "Invalid save slot"})
            return
        if slot_text and not 1 <= slot <= 99:
            self.send_json(400, {"error": "Invalid save slot"})
            return
        if save_key and (len(save_key) > 255 or slot_text):
            self.send_json(400, {"error": "Invalid save key"})
            return
        if method == "GET":
            with database() as connection:
                row = connection.execute(
                    "SELECT data, timestamp FROM saves WHERE account_id = ? AND game_id = ? AND slot = ? AND save_key = ?",
                    (account_id, game_id, slot, save_key),
                ).fetchone()
            if row is None:
                self.send_json(404, {"error": "Save not found"})
            else:
                self.send_json(200, {"data": base64.b64encode(row["data"]).decode("ascii"), "timestamp": row["timestamp"]})
        elif method == "PUT":
            value = self.read_json()
            encoded = value.get("data", "")
            if not isinstance(encoded, str):
                raise ValueError("Invalid save data")
            data = base64.b64decode(encoded, validate=True)
            if not data or len(data) > MAX_SAVE_BYTES:
                self.send_json(413, {"error": "Save is empty or exceeds the 4 MB limit"})
                return
            timestamp = int(time.time() * 1000)
            with database() as connection:
                usage = connection.execute(
                    "SELECT saves, bytes FROM account_usage WHERE account_id = ?", (account_id,)
                ).fetchone()
                used_saves = usage["saves"] if usage else 0
                used_bytes = usage["bytes"] if usage else 0
                replaced = None
                if used_saves >= MAX_ACCOUNT_SAVES or used_bytes + len(data) > MAX_ACCOUNT_STORAGE_BYTES:
                    # Only near a limit do we need the stored size of the row being overwritten.
                    replaced = connection.execute(
                        "SELECT length(data) AS bytes FROM saves WHERE account_id = ? AND game_id = ? AND slot = ? AND save_key = ?",
                        (account_id, game_id, slot, save_key),
                    ).fetchone()
                if used_saves >= MAX_ACCOUNT_SAVES and replaced is None:
                    self.send_json(413, {"error": "This account has reached its cloud save storage limit"})
                    return
                replaced_bytes = replaced["bytes"] if replaced else 0
                if used_bytes - replaced_bytes + len(data) > MAX_ACCOUNT_STORAGE_BYTES:
                    self.send_json(413, {"error": "This account has reached its cloud save storage limit"})
                    return
                connection.execute(
                    "INSERT INTO saves(account_id, game_id, slot, save_key, timestamp, data) VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(account_id, game_id, slot, save_key) DO UPDATE SET timestamp=excluded.timestamp, data=excluded.data",
                    (account_id, game_id, slot, save_key, timestamp, data),
                )
            self.send_json(200, {"timestamp": timestamp})
        elif method == "DELETE":
            with database() as connection:
                connection.execute(
                    "DELETE FROM saves WHERE account_id = ? AND game_id = ? AND slot = ? AND save_key = ?",
                    (account_id, game_id, slot, save_key),
                )
            self.send_json(204, None)
        else:
            self.send_json(405, {"error": "Method not allowed"})

    def _close_if_body_unread(self) -> None:
        """HTTP/1.1 keep-alive cannot survive an unread request body."""
        if self._body_consumed:
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length > 0:
            self.close_connection = True

    def send_json(self, status: int, value) -> None:
        body = b"" if value is None else json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        if status != 204:
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._close_if_body_unread()
        try:
            self.end_headers()
            if body:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True


class GirlLifeServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 256

    def __init__(self, *args, **kwargs):
        self._request_slots = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address) -> None:
        if not self._request_slots.acquire(blocking=False):
            self._reject_busy(request)
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._request_slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()

    @staticmethod
    def _reject_busy(request) -> None:
        body = b'{"error":"Server is busy; please try again"}'
        try:
            request.sendall(
                b"HTTP/1.1 503 Service Unavailable\r\n"
                b"Retry-After: 5\r\n"
                b"Content-Type: application/json; charset=utf-8\r\n"
                b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n"
                b"Connection: close\r\n\r\n" + body
            )
        except OSError:
            pass

    def handle_error(self, request, client_address) -> None:
        error = sys.exc_info()[1]
        if isinstance(error, (BrokenPipeError, ConnectionResetError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def accepts_gzip(header: str) -> bool:
    for item in header.split(","):
        coding, *parameters = item.strip().lower().split(";")
        if coding not in {"gzip", "*"}:
            continue
        try:
            if all(not parameter.strip().startswith("q=") or float(parameter.strip()[2:]) > 0 for parameter in parameters):
                return True
        except ValueError:
            continue
    return False


if __name__ == "__main__":
    os.umask(0o077)
    initialize_database()
    with GirlLifeServer(("127.0.0.1", LISTEN_PORT), GirlLifeHandler) as server:
        server.serve_forever()
