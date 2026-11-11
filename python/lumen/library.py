"""Reading history: recent documents, last position, progress, highlights and notes.

Stored in %LOCALAPPDATA%\\LumenReaderPy\\library.json. Documents are identified by a fingerprint of their
content, so notes survive renaming/moving a file; the last known path is used as a fallback (e.g. after
the file was edited). Files opened by drag & drop (the browser gives no path) are kept as a copy in the
library folder so they can be reopened from the recent list.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
from typing import Optional

from . import settings as settings_mod

MAX_RECENT = 30


def fingerprint(path: str) -> str:
    size = os.path.getsize(path)
    h = hashlib.sha1(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(1 << 20))
        if size > 2 << 20:
            f.seek(-(1 << 20), os.SEEK_END)
            h.update(f.read())
    return h.hexdigest()[:20]


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


class Library:
    def __init__(self, folder: Optional[str] = None):
        self.folder = folder or settings_mod.data_dir()
        self.file = os.path.join(self.folder, "library.json")
        self.files_dir = os.path.join(self.folder, "files")
        self.lock = threading.Lock()
        self.data = {"docs": {}, "recent": []}
        try:
            with open(self.file, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self.data["docs"] = loaded.get("docs") or {}
                self.data["recent"] = loaded.get("recent") or []
        except (OSError, ValueError):
            pass

    # ------------------------------------------------------------ persistence
    def save(self) -> None:
        tmp = self.file + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False)
            os.replace(tmp, self.file)
        except OSError:
            pass

    # ------------------------------------------------------------ documents
    def key_for(self, path: str) -> str:
        """Fingerprint key; when the content changed, the record of the same path is moved over."""
        key = fingerprint(path)
        with self.lock:
            docs = self.data["docs"]
            if key not in docs:
                norm = _norm(path)
                old = next((k for k, d in docs.items() if _norm(d.get("path", "")) == norm), None)
                if old:
                    docs[key] = docs.pop(old)
                    self.data["recent"] = [key if k == old else k for k in self.data["recent"]]
        return key

    def store_upload(self, temp_path: str, name: str) -> str:
        """Keeps a dropped file in the library folder; returns the permanent path."""
        os.makedirs(self.files_dir, exist_ok=True)
        key = fingerprint(temp_path)
        folder = os.path.join(self.files_dir, key)
        os.makedirs(folder, exist_ok=True)
        target = os.path.join(folder, os.path.basename(name) or "document")
        if not os.path.exists(target):
            shutil.copyfile(temp_path, target)
        return target

    def is_library_copy(self, path: str) -> bool:
        return _norm(path).startswith(_norm(self.files_dir) + os.sep)

    def touch(self, key: str, path: str, title: str, kind: str) -> dict:
        with self.lock:
            doc = self.data["docs"].setdefault(key, {})
            doc.update(path=path, title=title, kind=kind, opened=time.time())
            doc.setdefault("annotations", [])
            doc.setdefault("progress", 0)
            recent = [k for k in self.data["recent"] if k != key]
            recent.insert(0, key)
            for dropped in recent[MAX_RECENT:]:
                self._forget_copy(dropped)
            self.data["recent"] = recent[:MAX_RECENT]
            self.save()
            return dict(doc)

    def get(self, key: str) -> Optional[dict]:
        doc = self.data["docs"].get(key)
        return dict(doc) if doc else None

    def set_position(self, key: str, position: dict, progress: float) -> None:
        with self.lock:
            doc = self.data["docs"].get(key)
            if doc is not None:
                doc["position"] = position
                doc["progress"] = max(0.0, min(1.0, float(progress or 0)))
                self.save()

    def set_annotations(self, key: str, annotations: list) -> None:
        with self.lock:
            doc = self.data["docs"].get(key)
            if doc is not None:
                doc["annotations"] = annotations
                self.save()

    def set_word_copy(self, key: str, path: Optional[str]) -> None:
        with self.lock:
            doc = self.data["docs"].get(key)
            if doc is not None:
                doc["wordCopy"] = path
                self.save()

    def remove_recent(self, key: str) -> None:
        with self.lock:
            self.data["recent"] = [k for k in self.data["recent"] if k != key]
            self._forget_copy(key)
            self.save()

    def _forget_copy(self, key: str) -> None:
        """Deletes the library copy of a dropped file unless it carries notes."""
        doc = self.data["docs"].get(key)
        if not doc:
            return
        path = doc.get("path", "")
        if self.is_library_copy(path) and not doc.get("annotations"):
            shutil.rmtree(os.path.dirname(path), ignore_errors=True)
            self.data["docs"].pop(key, None)

    def recent(self) -> list[dict]:
        out = []
        for key in self.data["recent"]:
            doc = self.data["docs"].get(key)
            if not doc:
                continue
            out.append({
                "key": key,
                "title": doc.get("title", ""),
                "kind": doc.get("kind", ""),
                "ext": os.path.splitext(doc.get("path", ""))[1].lstrip(".").upper(),
                "path": doc.get("path", ""),
                "opened": doc.get("opened", 0),
                "progress": doc.get("progress", 0),
                "notes": len(doc.get("annotations") or []),
                "exists": os.path.isfile(doc.get("path", "")),
            })
        return out
