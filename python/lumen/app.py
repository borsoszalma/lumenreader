"""Entry point: starts the local server and opens the reader in an Edge app window."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
import webbrowser

from . import settings as settings_mod
from .server import ReaderApp, start_server

log = logging.getLogger("lumen")


def find_browser() -> str | None:
    """Microsoft Edge (part of Windows); Chrome as an alternative."""
    candidates = []
    for env in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            candidates.append(os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"))
    for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            candidates.append(os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"))
    candidates += [shutil.which(n) for n in ("msedge", "microsoft-edge", "google-chrome", "chromium")]
    return next((c for c in candidates if c and os.path.isfile(c)), None)


def open_window(url: str) -> None:
    browser = find_browser()
    if browser:
        profile = os.path.join(settings_mod.data_dir(), "browser-profile")
        flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
        try:
            subprocess.Popen([browser, f"--app={url}", f"--user-data-dir={profile}", "--no-first-run",
                              "--no-default-browser-check", "--window-size=1200,820"],
                             creationflags=flags, close_fds=True)
            return
        except OSError:
            log.exception("browser start failed")
    webbrowser.open(url)


def main(argv: list[str]) -> int:
    logging.basicConfig(filename=os.path.join(settings_mod.data_dir(), "log.txt"), level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")
    app = ReaderApp(settings_mod.load())
    start_file = next((a for a in argv if os.path.isfile(a)), None)
    if start_file:
        app.open_path(os.path.abspath(start_file))

    server, url = start_server(app)
    log.info("listening on %s", url.split("/", 3)[2])
    if "--no-browser" in argv:
        print(url, flush=True)
    else:
        open_window(url)

    # Stay alive while the window is open (the page pings every few seconds).
    try:
        while True:
            time.sleep(1)
            now = time.time()
            if app.last_ping is None:
                if now - app.started > 120:
                    break
            elif now - app.last_ping > 25:
                break
    except KeyboardInterrupt:
        pass
    server.shutdown()
    app.close()
    return 0
