"""Extract the distributable and exercise its own assets and real native model."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    package = ROOT / "xiaqi-demo-windows.zip"
    expected = (ROOT / "xiaqi-demo-windows.sha256").read_text(encoding="ascii").split()[0]
    assert hashlib.sha256(package.read_bytes()).hexdigest() == expected
    scratch = ROOT / "data"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="package-check-", dir=scratch) as temporary:
        directory = Path(temporary)
        with zipfile.ZipFile(package) as archive:
            assert archive.testzip() is None
            for name in archive.namelist():
                assert (directory / name).resolve().is_relative_to(directory.resolve())
                assert "/train/" not in name and "/.venv/" not in name and not name.endswith(".db")
            archive.extractall(directory)
        extracted = directory / "xiaqi-demo"
        env = os.environ.copy()
        env["PYTHONPATH"] = str(extracted / "backend")
        env["XIAQI_DB"] = str(extracted / "data" / "verification.db")
        code = '''
from pathlib import Path
from fastapi.testclient import TestClient
from app.config import ROOT
from app.main import create_app
assert ROOT == Path.cwd()
assert (ROOT / "start-demo.cmd").is_file()
with TestClient(create_app()) as client:
    assert client.get("/").status_code == 200
    assert client.get("/demo-assets/main.js").status_code == 200
    overview = client.get("/api/demo").json()
    assert all(model["available"] for model in overview["models"])
    assert overview["primary"]["wins"] == 139
    assert client.get("/api/demo/replays/199").json()["status"] == "finished"
    game = client.post("/api/games", json={"black_id":"demo:trained", "white_id":"human"}).json()
    moved = client.post("/api/games/" + game["id"] + "/step", json={"think_ms":2500}).json()
    assert moved["move_count"] == 1 and moved["status"] == "playing"
    assert moved["moves"][0]["action"] == "PLACE"
    assert client.get("/arena").status_code == 200
'''
        subprocess.run([sys.executable, "-c", code], cwd=extracted, env=env, check=True)
    print("Standalone ZIP verified: checksum, extraction, assets, frozen models, replay and actual legal AI move.")


if __name__ == "__main__":
    main()
