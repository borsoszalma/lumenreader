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
from .formats import docx_export, pdf_export
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
                    annotations=doc.get("annotations") or [], copy=self.copy_kind(self.session),
                    copyPath=doc.get("copy") or doc.get("wordCopy"))
        return info

    @staticmethod
    def copy_kind(session: Session) -> Optional[str]:
        """'docx' / 'pdf' when notes are also written into a copy of the document."""
        if session.docx_source:
            return "docx"
        if session.kind == "pdf":
            if not hasattr(session, "_pdf_ok"):
                session._pdf_ok = pdf_export.can_annotate(session.path)
            return "pdf" if session._pdf_ok else None
        return None

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
        """Stores the notes; Word and PDF documents also get an annotated copy next to the original.
        A copy that was changed outside the app (e.g. edited in Word) is never overwritten."""
        self.library.set_annotations(key, annotations)
        session = self.session
        kind = self.copy_kind(session) if session and key == self.session_key else None
        if not kind:
            return {"ok": True}
        doc = self.library.get(key) or {}
        dst = doc.get("copy") or doc.get("wordCopy") or self.copy_path(session, kind)
        if not os.path.exists(dst) and not annotations:
            return {"ok": True}
        changed = False
        stamp = doc.get("copyStamp")
        if os.path.exists(dst) and stamp and file_stamp(dst) != stamp:
            dst = free_name(self.copy_path(session, kind))
            changed = True

        def write(target):
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if kind == "docx":
                docx_export.export(session.docx_source, target, annotations, author=user_display_name())
            else:
                pdf_export.export(session.path, target, annotations, author=user_display_name())

        name = os.path.basename(dst)
        try:
            write(dst)
        except PermissionError:
            if os.path.exists(dst):  # the copy is open in Word / a PDF viewer
                return {"ok": True, "copyError": self.t("WordSaveFailed").format(name)}
            dst = free_name(os.path.join(documents_dir(), "Lumen Reader", name))
            try:
                write(dst)
            except Exception as e:
                log.exception("copy export")
                return {"ok": True, "copyError": f"{self.t('WordSaveFailed').format(name)}\n{e}"}
        except Exception as e:
            log.exception("copy export")
            return {"ok": True, "copyError": f"{self.t('WordSaveFailed').format(name)}\n{e}"}
        self.library.set_copy(key, dst, file_stamp(dst))
        return {"ok": True, "copy": dst, "copyChanged": changed}

    def search(self, q: str) -> dict:
        """EPUB: match positions in every chapter (the browser only has the current one)."""
        session = self.session
        q = (q or "").strip().lower()
        if not session or session.kind != "epub" or len(q) < 2:
            return {"chapters": []}
        from .formats import epub

        wanted: dict[str, set] = {}

        def collect(items):
            for it in items or []:
                if it.href and "#" in it.href:
                    path, frag = it.href.split("#", 1)
                    wanted.setdefault(path, set()).add(frag)
                collect(it.children)

        collect(session.toc)
        chapters = []
        for path in session.spine:
            text, ids = epub.chapter_text(session, path)
            low = text.lower()
            positions = []
            i = low.find(q)
            while i >= 0 and len(positions) < 5000:
                positions.append(i)
                i = low.find(q, i + len(q))
            frags = {f: ids[f] for f in wanted.get(path, ()) if f in ids}
            chapters.append({"path": path, "positions": positions, "ids": frags})
        return {"chapters": chapters}

    def copy_path(self, session: Session, kind: str) -> str:
        title = os.path.splitext(os.path.basename(session.path))[0]
        name = f"{title} ({self.t('WithNotes')}).{kind}"
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


def file_stamp(path: str) -> Optional[str]:
    try:
        st = os.stat(path)
        return f"{st.st_size}:{st.st_mtime_ns}"
    except OSError:
        return None


def free_name(path: str) -> str:
    """'X (with notes).docx' → 'X (with notes 2).docx', 3, … whichever does not exist yet."""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    stem = base[:-1] if base.endswith(")") else base
    for n in range(2, 1000):
        candidate = f"{stem} {n}){ext}" if base.endswith(")") else f"{base} ({n}){ext}"
        if not os.path.exists(candidate):
            return candidate
    return path


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
            if rel == "api/backup":
                body = json.dumps(app.library.export_data(), ensure_ascii=False, indent=1).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Content-Disposition",
                                 f'attachment; filename="lumen-reader-{time.strftime("%Y-%m-%d")}.json"')
                self.end_headers()
                self.wfile.write(body)
                return
            if rel == "api/search":
                q = (parse_qs(urlparse(self.path).query).get("q") or [""])[0]
                return self._json(app.search(q))
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
            if rel == "api/restore":
                try:
                    n = app.library.import_data(json.loads(body or b"{}"))
                    return self._json({"imported": n, "recent": app.library.recent()})
                except (ValueError, AttributeError):
                    return self._json({"error": app.t("BackupInvalid")})
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
