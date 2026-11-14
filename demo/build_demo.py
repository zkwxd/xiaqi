"""Verify frozen evidence and build a reproducible standalone Windows package."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def verify() -> None:
    models = ROOT / "demo" / "models"
    manifest = json.loads((models / "manifest.json").read_text(encoding="utf-8"))
    for relative, key in (("bot.exe", "executable_sha256"),
                          ("trained/weights.txt", "trained_sha256"),
                          ("baseline/weights.txt", "baseline_sha256")):
        actual = hashlib.sha256((models / relative).read_bytes()).hexdigest()
        if actual != manifest[key]:
            raise ValueError(f"Frozen model checksum mismatch: {relative}")
    source_hash = hashlib.sha256((models / "main.cpp").read_bytes()).hexdigest()
    if source_hash != "dc40b2e5b2d4ec6b3780b41d9f850dd72f90e95225f89f39f737fa085a6ac7ce":
        raise ValueError("Frozen C++ source checksum mismatch")
    from app.demo import replay, results
    data = results()
    records = data["records"]
    if len(records) != data["primary"]["games"] or sum(r["result"] > 0 for r in records) != data["primary"]["wins"]:
        raise ValueError("Evaluation summary does not match complete game records")
    for index in range(len(records)):
        replay(index)
    print(f"Frozen program, C++ source, both models and {len(records)} complete replays verified.")


def package() -> Path:
    verify()
    files = [(ROOT / "demo" / "README.md", "README.md"),
             (ROOT / "demo" / "start-demo.cmd", "start-demo.cmd"),
             (ROOT / "demo" / "launch.py", "demo/launch.py"),
             (ROOT / "requirements.txt", "requirements.txt")]
    for directory, pattern in (("backend/app", "*.py"), ("frontend", "*"),
                               ("demo/web", "*"), ("demo/data", "*"), ("demo/models", "*")):
        files.extend((path, path.relative_to(ROOT).as_posix())
                     for path in (ROOT / directory).rglob(pattern)
                     if path.is_file() and "__pycache__" not in path.parts)
    output = ROOT / "xiaqi-demo-windows.zip"
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source, name in sorted(files, key=lambda item: item[1]):
            info = zipfile.ZipInfo("xiaqi-demo/" + name, date_time=(2026, 11, 9, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 0
            info.external_attr = 0o644 << 16
            content = source.read_bytes()
            if source.suffix in {".py", ".js", ".css", ".html", ".json", ".md", ".cmd", ".txt"} and source.name != "weights.txt":
                content = content.replace(b"\r\n", b"\n")
                if source.suffix == ".cmd":
                    content = content.replace(b"\n", b"\r\n")
            archive.writestr(info, content, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    (ROOT / "xiaqi-demo-windows.sha256").write_text(f"{digest}  {output.name}\n", encoding="ascii")
    print(f"Package: {output.name}, {output.stat().st_size:,} bytes\nSHA256: {digest}")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", action="store_true", help="Build the package (default).")
    parser.add_argument("--verify", action="store_true", help="Only verify frozen models and all evaluation replays.")
    args = parser.parse_args()
    verify() if args.verify else package()
