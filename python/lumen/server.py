"""Local HTTP server (127.0.0.1 only) that serves the reader UI and the opened document."""
from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

import subprocess
import sys

from . import formats, i18n, settings as settings_mod
from .formats import docx_export
from .formats.common import DocumentError, Session
from .library import Library

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
log = logging.getLogger("lumen")


class ReaderApp:
    """State shared by all requests: settings, language, the current document."""

    def __init__(self, settings: dict):
        self.settings = settings
        self.token = secrets.token_urlsafe(18)
        self.lang = i18n.resolve(settings.get("language", "auto"))
        self.strings = i18n.load(self.lang)
        self.session: Optional[Session] = None
        self.session_key: Optional[str] = None
        self.library = Library()
        self.lock = threading.Lock()
        self.last_ping: Optional[float] = None
        self.started = time.time()
        self.upload_dir = tempfile.mkdtemp(prefix="lumen-upload-")

    # ------------------------------------------------------------ helpers
    def t(self, key: str) -> str:
        return self.strings.get(key) or i18n.load("en").get(key, key)

    def state(self) -> dict:
        return {
            "lang": self.lang,
            "languages": [{"code": c, "name": n} for c, n in i18n.LANGUAGES],
            "strings": self.strings,
            "settings": self.settings,
            "session": self.session_info() if self.session else None,
            "extensions": formats.EXTENSIONS,
            "recent": self.library.recent(),
        }

    def session_info(self) -> dict:
        info = self.session.info()
        doc = self.library.get(self.session_key) or {}
        info.update(key=self.session_key, position=doc.get("position"), progress=doc.get("progress", 0),
                    annotations=doc.get("annotations") or [], wordCopy=doc.get("wordCopy"))
        return info

    def update_settings(self, changes: dict) -> dict:
        allowed = {"theme", "language", "tocVisible", "tocWidth", "reopenLast"}
        for k, v in changes.items():
            if k in allowed:
                self.settings[k] = v
        if "language" in changes:
            self.lang = i18n.resolve(self.settings.get("language", "auto"))
            self.strings = i18n.load(self.lang)
        settings_mod.save(self.settings)
        return self.state()

    def open_key(self, key: str) -> dict:
        doc = self.library.get(key)
        if not doc:
            return {"error": self.t("ErrorTitle")}
        if not os.path.isfile(doc.get("path", "")):
            return {"error": self.t("FileMissing").format(doc.get("path", ""))}
        return self.open_path(doc["path"])

    def open_path(self, path: str) -> dict:
        if not path or not os.path.isfile(path):
            return {"error": self.t("FileMissing").format(path or "")}
        if not formats.is_supported(path):
            return {"error": self.t("UnsupportedFormat").format(os.path.splitext(path)[1])}
        try:
            key = self.library.key_for(path)
            session = formats.load(path, self.t, os.path.join(self.library.folder, "converted", key))
        except DocumentError as e:
            msg = self.t(e.key)
            return {"error": msg.format(e.arg) if "{0}" in msg else msg}
        except Exception as e:  # damaged or unexpected file
            log.exception("open failed: %s", path)
            return {"error": f"{self.t('ErrorTitle')}\n{e}"}
        with self.lock:
            old, self.session, self.session_key = self.session, session, key
        if old:
            old.close()
        self.library.touch(key, path, session.title, session.kind)
        if not self.library.is_library_copy(path):
            self.settings["lastFolder"] = os.path.dirname(os.path.abspath(path))
            settings_mod.save(self.settings)
        return {"session": self.session_info()}

    def save_upload(self, name: str, data: bytes) -> str:
        """Dropped files have no path in the browser: keep a copy in the library folder."""
        name = os.path.basename(name.replace("\\", "/")) or "document"
        folder = tempfile.mkdtemp(dir=self.upload_dir)
        path = os.path.join(folder, name)
        with open(path, "wb") as f:
            f.write(data)
        if not formats.is_supported(path):
            return path
        return self.library.store_upload(path, name)

    # ------------------------------------------------------------ reading position, notes, Word copy
    def save_position(self, key: str, position: dict, progress: float) -> None:
        self.library.set_position(key, position, progress)

    def save_annotations(self, key: str, annotations: list) -> dict:
        self.library.set_annotations(key, annotations)
        session = self.session
        if not session or key != self.session_key or not session.docx_source:
            return {"ok": True}
        dst = self.word_copy_path(session)
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            docx_export.export(session.docx_source, dst, annotations, author=user_display_name())
        except PermissionError:
            if os.path.exists(dst):  # the copy is open in Word
                return {"ok": True, "wordError": self.t("WordSaveFailed").format(os.path.basename(dst))}
            dst = os.path.join(documents_dir(), "Lumen Reader", os.path.basename(dst))
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                docx_export.export(session.docx_source, dst, annotations, author=user_display_name())
            except Exception as e:
                log.exception("word export")
                return {"ok": True, "wordError": f"{self.t('WordSaveFailed').format(os.path.basename(dst))}\n{e}"}
        except Exception as e:
            log.exception("word export")
            return {"ok": True, "wordError": f"{self.t('WordSaveFailed').format(os.path.basename(dst))}\n{e}"}
        self.library.set_word_copy(key, dst)
        return {"ok": True, "wordCopy": dst}

    def word_copy_path(self, session: Session) -> str:
        title = os.path.splitext(os.path.basename(session.path))[0]
        name = f"{title} ({self.t('WithNotes')}).docx"
        if self.library.is_library_copy(session.path):
            return os.path.join(documents_dir(), "Lumen Reader", name)
        return os.path.join(os.path.dirname(os.path.abspath(session.path)), name)

    def choose_file(self) -> dict:
        """Native open dialog through tkinter (part of the standard Python installer)."""
        try:
            import tkinter as tk
            from tkinter import filedialog
        except ImportError:
            return {"unavailable": True}
        patterns = " ".join("*" + e for e in formats.EXTENSIONS)
        result: dict = {}

        def run():
            try:
                root = tk.Tk()
                root.withdraw()
                root.attributes("-topmost", True)
                path = filedialog.askopenfilename(
                    parent=root, title="Lumen Reader",
                    initialdir=self.settings.get("lastFolder") or os.path.expanduser("~"),
                    filetypes=[(self.t("FileFilterAll"), patterns), ("*.*", "*.*")])
                root.destroy()
                result["path"] = path
            except Exception:
                log.exception("file dialog")
                result["unavailable"] = True

        th = threading.Thread(target=run)
        th.start()
        th.join()
        if result.get("unavailable"):
            return {"unavailable": True}
        if not result.get("path"):
            return {"cancelled": True}
        return self.open_path(result["path"])

    def close(self) -> None:
        if self.session:
            self.session.close()
        shutil.rmtree(self.upload_dir, ignore_errors=True)


def documents_dir() -> str:
    if sys.platform == "win32":
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(260)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:  # CSIDL_PERSONAL
                return buf.value
        except Exception:
            pass
    return os.path.join(os.path.expanduser("~"), "Documents")


def user_display_name() -> str:
    """Comment author shown in Word: the Windows display name, else the user name."""
    if sys.platform == "win32":
        try:
            import ctypes
            size = ctypes.c_ulong(256)
            buf = ctypes.create_unicode_buffer(256)
            if ctypes.windll.secur32.GetUserNameExW(3, buf, ctypes.byref(size)) and buf.value.strip():
                return buf.value.strip()
        except Exception:
            pass
    return os.environ.get("USERNAME") or os.environ.get("USER") or "Lumen Reader"


def reveal(path: str) -> None:
    """Shows the file in Explorer."""
    if not path or not os.path.exists(path):
        return
    if sys.platform == "win32":
        subprocess.Popen(["explorer.exe", "/select,", os.path.normpath(path)])
    else:
        webbrowser.open("file://" + os.path.dirname(path))


def make_handler(app: ReaderApp):
    prefix = f"/{app.token}/"

    class Handler(BaseHTTPRequestHandler):
        server_version = "LumenReader"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # keep quiet
            log.debug(fmt, *args)

        # ------------------------------------------------------------ plumbing
        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost")

        def _send(self, status: int, body: bytes, ctype: str, cache: bool = False):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=3600" if cache else "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, obj, status=200):
            self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _body(self) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(length) if length > 0 else b""

        def _route(self) -> Optional[str]:
            path = urlparse(self.path).path
            if not self._host_ok() or not path.startswith(prefix):
                self._send(404, b"Not found", "text/plain")
                return None
            return unquote(path[len(prefix):])

        # ------------------------------------------------------------ GET
        def do_GET(self):
            rel = self._route()
            if rel is None:
                return
            if rel in ("", "index.html"):
                rel = "index.html"
            if rel == "api/state":
                return self._json(app.state())
            if rel.startswith("doc/"):
                parts = rel.split("/", 2)
                session = app.session
                if session and len(parts) == 3 and parts[1] == session.id:
                    try:
                        res = session.serve(parts[2])
                    except Exception:
                        log.exception("serve")
                        res = None
                    if res:
                        return self._send(200, res[0], res[1], cache=True)
                return self._send(404, b"Not found", "text/plain")
            # static UI files (also lumen/web/pdfjs/...)
            if not rel.startswith(".") and ".." not in rel:
                full = formats.common.safe_join(WEB_DIR, rel)
                if full:
                    with open(full, "rb") as f:
                        data = f.read()
                    return self._send(200, data, formats.common.mime_for(full) + (
                        "; charset=utf-8" if rel.endswith((".html", ".js", ".mjs", ".css")) else ""),
                        cache=rel.startswith("pdfjs/"))
            self._send(404, b"Not found", "text/plain")

        do_HEAD = do_GET

        # ------------------------------------------------------------ POST
        def do_POST(self):
            rel = self._route()
            if rel is None:
                return
            # Only our own page may call the API (blocks cross-site requests from other websites).
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).hostname not in ("127.0.0.1", "localhost"):
                return self._send(403, b"Forbidden", "text/plain")
            body = self._body()
            query = parse_qs(urlparse(self.path).query)

            if rel == "api/ping":
                app.last_ping = time.time()
                return self._json({"ok": True})
            if rel == "api/bye":
                app.last_ping = time.time() - 20  # exit soon unless the page pings again (reload)
                return self._json({"ok": True})
            if rel == "api/settings":
                return self._json(app.update_settings(json.loads(body or b"{}")))
            if rel == "api/choose":
                return self._json(app.choose_file())
            if rel == "api/open":
                req = json.loads(body or b"{}")
                if req.get("key"):
                    return self._json(app.open_key(req["key"]))
                return self._json(app.open_path(req.get("path", "")))
            if rel == "api/position":
                req = json.loads(body or b"{}")
                app.save_position(req.get("key", ""), req.get("position") or {}, req.get("progress") or 0)
                return self._json({"ok": True})
            if rel == "api/annotations":
                req = json.loads(body or b"{}")
                return self._json(app.save_annotations(req.get("key", ""), req.get("annotations") or []))
            if rel == "api/forget":
                app.library.remove_recent(json.loads(body or b"{}").get("key", ""))
                return self._json({"recent": app.library.recent()})
            if rel == "api/reveal":
                reveal(json.loads(body or b"{}").get("path", ""))
                return self._json({"ok": True})
            if rel == "api/upload":
                name = (query.get("name") or ["document"])[0]
                return self._json(app.open_path(app.save_upload(name, body)))
            if rel == "api/external":
                url = json.loads(body or b"{}").get("url", "")
                if urlparse(url).scheme in ("http", "https", "mailto"):
                    webbrowser.open(url)
                return self._json({"ok": True})
            self._send(404, b"Not found", "text/plain")

    return Handler


def start_server(app: ReaderApp) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    server.daemon_threads = True
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{port}/{app.token}/"
