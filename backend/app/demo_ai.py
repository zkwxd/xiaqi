"""Run the frozen, evaluated native models without Docker or a training runtime."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from functools import lru_cache
from pathlib import Path

from .config import ROOT
from .protocol import DecisionTimeout, ProtocolError, parse_decision
from .rule_engine import RuleEngine, board_to_rows

DEMO_NAMES = {"demo:trained": "夏棋 · 强化版", "demo:baseline": "夏棋 · 原始版"}
MODEL_ROOT = ROOT / "demo" / "models"


@lru_cache(maxsize=12)
def _sha256(path: str, modified: int, size: int) -> str:
    del modified, size
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def model_status(player_id: str) -> dict:
    key = "trained" if player_id == "demo:trained" else "baseline"
    try:
        manifest = json.loads((MODEL_ROOT / "manifest.json").read_text(encoding="utf-8"))
        for path, expected in (
            (MODEL_ROOT / "bot.exe", manifest["executable_sha256"]),
            (MODEL_ROOT / key / "weights.txt", manifest[key + "_sha256"]),
        ):
            stat = path.stat()
            if _sha256(str(path), stat.st_mtime_ns, stat.st_size) != expected:
                raise ValueError("模型文件校验未通过")
        if os.name != "nt":
            raise ValueError("演示程序需要 Windows")
        return {"id": player_id, "name": DEMO_NAMES[player_id], "available": True}
    except (OSError, ValueError, KeyError) as exc:
        return {"id": player_id, "name": DEMO_NAMES[player_id], "available": False,
                "detail": str(exc) if isinstance(exc, ValueError) else "演示模型文件缺失，请重新解压完整 Demo"}


def arena_request(state, request_id: str, legal: list[dict], *, warmup: bool = False, last=None, captures=None) -> dict:
    return {
        "protocol": "arena.v1", "type": "move_request", "request_id": request_id,
        "game": "go9", "rule_version": "go9-chinese-v2", "player": state.to_play,
        "time_limit_ms": 10000,
        "observation": {
            "size": 9, "komi": 7.5, "board": list(reversed(board_to_rows(state.board))),
            "current_player": state.to_play, "move_number": state.move_count,
            "pass_count": state.consecutive_passes, "passes_to_score": 2,
            "captures": dict(captures or {"1": 0, "2": 0}),
            "pass_forbidden_after_dispute": warmup, "last_action": last,
            "legal_moves": [{"row": 8 - move["y"], "col": move["x"]} for move in legal],
        },
    }


class DemoAgent:
    is_builtin = False

    def __init__(self, player_id: str):
        self.player_id = player_id

    def raw_decision(self, payload: dict, timeout_s: float) -> dict:
        status = model_status(self.player_id)
        if not status["available"]:
            raise ProtocolError(status["detail"])
        engine = RuleEngine()
        state = engine.new_game()
        requests = []
        last = None
        captures = {"1": 0, "2": 0}
        # Restore every actual position before searching. Empty legal lists with
        # pass blocked cause the native program to record history without search.
        # This preserves positional superko even after reopening a saved game.
        for index, move in enumerate(payload.get("moves", [])):
            requests.append(arena_request(state, f"history-{index}", [], warmup=True, captures=captures))
            action = parse_decision(move)
            color = str(state.to_play)
            state, info = engine.apply(state, action)
            captures[color] += len(info.captured)
            last = {"kind": action.type}
            if action.type == "PLACE":
                last.update(row=8 - action.y, col=action.x)
        if board_to_rows(state.board) != payload["board"] or ("B" if state.to_play == 1 else "W") != payload["to_play"]:
            raise ProtocolError("棋盘与历史不一致，无法调用演示模型")
        legal = [{"x": x, "y": y} for x, y in engine.legal_places(state)]
        request_id = f"move-{state.move_count}"
        requests.append(arena_request(state, request_id, legal, last=last, captures=captures))
        env = os.environ.copy()
        env.update(GO9_SIMS="32", GO9_SECONDS="2.5", GO9_FARM="1")
        for flag in ("GO9_DEBUG", "GO9_TRACE"):
            env.pop(flag, None)
        key = "trained" if self.player_id == "demo:trained" else "baseline"
        try:
            completed = subprocess.run(
                [str(MODEL_ROOT / "bot.exe")], cwd=MODEL_ROOT / key, env=env,
                input="".join(json.dumps(req, separators=(",", ":")) + "\n" for req in requests),
                text=True, encoding="utf-8", errors="replace", capture_output=True,
                timeout=timeout_s, check=False,
                **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
            )
        except subprocess.TimeoutExpired as exc:
            raise DecisionTimeout() from exc
        except OSError as exc:
            raise ProtocolError("无法启动演示模型") from exc
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        if completed.returncode or len(lines) != len(requests):
            raise ProtocolError("演示模型没有返回完整响应")
        try:
            response = json.loads(lines[-1])
            if any(response.get(k) != v for k, v in (
                ("protocol", "arena.v1"), ("type", "move_response"), ("request_id", request_id),
            )) or len(lines[-1].encode("utf-8")) > 4096:
                raise ValueError("invalid response")
            action = response["action"]
            if action["kind"] == "PLACE":
                row, col = action["row"], action["col"]
                if type(row) is not int or type(col) is not int or not (0 <= row < 9 and 0 <= col < 9):
                    raise ValueError("invalid coordinates")
                return {"action": "PLACE", "x": col, "y": 8 - row}
            if action["kind"] in ("PASS", "RESIGN"):
                return {"action": action["kind"]}
            raise ValueError("invalid action")
        except (ValueError, TypeError, KeyError) as exc:
            raise ProtocolError("演示模型响应格式错误") from exc
