"""Read-only training evidence and complete, rule-checked evaluation replays."""

from __future__ import annotations

import json
from functools import lru_cache

from .config import ROOT
from .demo_ai import DEMO_NAMES, model_status
from .rule_engine import Action, RuleEngine, board_to_rows, color_name
from .coords import to_vertex


@lru_cache(maxsize=1)
def results() -> dict:
    return json.loads((ROOT / "demo" / "data" / "results.json").read_text(encoding="utf-8"))


def overview() -> dict:
    data = results()
    return {**{key: value for key, value in data.items() if key != "records"},
            "models": [model_status(player_id) for player_id in DEMO_NAMES],
            "replays": [{"id": i, "pair": record["pair"], "candidate_black": record["candidate_black"],
                         "result": record["result"], "moves": len(record["moves"])}
                        for i, record in enumerate(data["records"])]}


@lru_cache(maxsize=200)
def replay(index: int) -> dict:
    records = results()["records"]
    if index < 0 or index >= len(records):
        raise IndexError(index)
    record = records[index]
    engine = RuleEngine()
    state = engine.new_game()
    frames = [{"move_number": 0, "board": board_to_rows(state.board), "last_move": None,
               "to_play": "black", "finished": False}]
    moves = []
    last = None
    for number, point in enumerate(record["moves"], 1):
        action = Action.pass_() if point == 81 else Action.place(point % 9, point // 9)
        color = color_name(state.to_play)
        state, info = engine.apply(state, action)
        vertex = to_vertex(action.x, action.y) if action.type == "PLACE" else None
        move = {"move_number": number, "color": color, "action": action.type,
                "x": action.x, "y": action.y, "vertex": vertex, "captured": len(info.captured)}
        moves.append(move)
        if vertex:
            last = {"x": action.x, "y": action.y, "vertex": vertex, "color": color}
        frames.append({"move_number": number, "board": board_to_rows(state.board), "last_move": last,
                       "to_play": None if state.finished else color_name(state.to_play), "finished": state.finished})
    if not state.finished or state.winner != record["winner"]:
        raise ValueError("评测棋谱与记录的结果不一致")
    black = "demo:trained" if record["candidate_black"] else "demo:baseline"
    white = "demo:baseline" if record["candidate_black"] else "demo:trained"
    return {
        "id": f"evaluation-{index}", "replay_id": index, "readonly": True, "mode": "evaluation",
        "black": {"id": black, "name": DEMO_NAMES[black]}, "white": {"id": white, "name": DEMO_NAMES[white]},
        "status": "finished", "to_play": None, "move_count": state.move_count,
        "board": board_to_rows(state.board), "last_move": last, "legal_moves": [],
        "moves": moves, "frames": frames, "analysis": None,
        "result": {"winner": color_name(state.winner), "end_reason": state.end_reason,
                   "win_stones": state.score.win_stones, "score": state.score.to_dict(), "forfeit": False},
        "created_at": results()["released_at"], "finished_at": results()["released_at"],
    }
