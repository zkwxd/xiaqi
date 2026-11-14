"""Start the local demo, reuse a running instance, and open its browser page."""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("XIAQI_DB", str(ROOT / "data" / "demo.db"))


def main():
    import uvicorn
    for port in range(8000, 8011):
        url = f"http://127.0.0.1:{port}"
        try:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", port))
            break
        except OSError:
            try:
                with urllib.request.urlopen(url + "/api/demo", timeout=1) as response:
                    if json.load(response).get("title") == "夏棋 · 强化学习演示":
                        if "--no-browser" not in sys.argv:
                            webbrowser.open(url)
                        print(f"Demo is already running: {url}")
                        return
            except Exception:
                continue
    else:
        raise RuntimeError("Ports 8000–8010 are occupied. Close a local service and try again.")
    print(f"Xiaqi demo: {url}\nKeep this window open. Press Ctrl+C to stop.")
    if "--no-browser" not in sys.argv:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    uvicorn.run("app.main:app", host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
