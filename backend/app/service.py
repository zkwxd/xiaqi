"""对局服务：存档、人类落子、竞赛外层叫步、复盘与棋谱。"""

from __future__ import annotations

import json
import logging
import threading
import uuid

from .agents import BUILTIN_IDS, BUILTIN_NAMES, HttpAgent, StdioAgent
from .analysis import analyze_record, format_number
from .coords import to_vertex
from .database import Database, utc_now
from .demo_ai import DEMO_NAMES, DemoAgent, model_status
from .protocol import ContestLayer, ContestLoss, ProtocolError, parse_decision
from .rule_engine import (
    BLACK,
    WHITE,
    Action,
    GameState,
    IllegalMove,
    RuleEngine,
    board_to_rows,
    color_code,
    color_name,
    other,
)

logger = logging.getLogger("xiaqi")


class GameError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class GameService:
    def __init__(self, database: Database):
        self.db = database
        self.engine = RuleEngine()
        self.contest = ContestLayer()
        self._locks: dict[str, threading.RLock] = {}
        self._lock_guard = threading.Lock()

    def list_agents(self) -> list[dict]:
        custom = []
        for row in self.db.list_agents():
            custom.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "protocol": row["protocol"],
                    "command": row["command"],
                    "endpoint": row["endpoint"],
                }
            )
        builtins = [
            {"id": "human", "name": "人类", "protocol": "human"},
            {"id": "builtin:heuristic", "name": "启发式", "protocol": "builtin"},
            {"id": "builtin:mcts", "name": "蒙特卡洛", "protocol": "builtin"},
            {"id": "builtin:random", "name": "随机", "protocol": "builtin"},
        ]
        demos = [{**model_status(player_id), "protocol": "demo"} for player_id in DEMO_NAMES]
        return builtins[:1] + demos + builtins[1:] + custom

    def register_agent(self, name: str, protocol: str, command: str | None, endpoint: str | None) -> dict:
        cleaned = (name or "").strip()
        if not cleaned or len(cleaned) > 40:
            raise GameError(400, "名字需要 1 到 40 个字")
        protocol = (protocol or "").strip().lower()
        if protocol == "stdio":
            command = (command or "").strip()
            if not command:
                raise GameError(400, "需要可执行命令")
            if len(command) > 500 or "\n" in command or "\r" in command:
                raise GameError(400, "命令不合法")
            endpoint = None
        elif protocol == "http":
            endpoint = (endpoint or "").strip()
            if not endpoint.startswith(("http://", "https://")):
                raise GameError(400, "地址需要以 http:// 或 https:// 开头")
            command = None
        else:
            raise GameError(400, "协议只能是 stdio 或 http")
        row = {
            "id": "agent-" + uuid.uuid4().hex[:8],
            "name": cleaned,
            "protocol": protocol,
            "command": command,
            "endpoint": endpoint,
            "created_at": utc_now(),
        }
        self.db.create_agent(row)
        return {
            "id": row["id"],
            "name": row["name"],
            "protocol": row["protocol"],
            "command": row["command"],
            "endpoint": row["endpoint"],
        }

    def delete_agent(self, agent_id: str) -> None:
        if agent_id in BUILTIN_IDS or agent_id in DEMO_NAMES or agent_id == "human":
            raise GameError(400, "内置对局者不能删除")
        if not self.db.delete_agent(agent_id):
            raise GameError(404, "Agent 不存在")

    def create_game(self, black_id: str, white_id: str) -> dict:
        self._ensure_player(black_id)
        self._ensure_player(white_id)
        game_id = uuid.uuid4().hex[:12]
        self.db.create_game(
            {
                "id": game_id,
                "mode": _mode(black_id, white_id),
                "black_id": black_id,
                "white_id": white_id,
                "black_name": self._player_name(black_id),
                "white_name": self._player_name(white_id),
                "created_at": utc_now(),
            }
        )
        return self.get_game(game_id)

    def list_games(self) -> list[dict]:
        return [_summary(row) for row in self.db.list_games()]

    def get_game(self, game_id: str) -> dict:
        row = self._row(game_id)
        moves = self.db.list_moves(game_id)
        state, frames = self._walk(moves)
        return self._view(row, moves, state, frames)

    def play_move(self, game_id: str, action_name: str, x: int | None, y: int | None, vertex: str | None) -> dict:
        with self._game_lock(game_id):
            row = self._row(game_id)
            moves = self.db.list_moves(game_id)
            state, _frames = self._walk(moves)
            if state.finished:
                raise GameError(400, "对局已结束")
            if self._seat_id(row, state.to_play) != "human":
                raise GameError(400, "当前不是人类回合")
            try:
                action = parse_decision({"action": action_name, "x": x, "y": y, "vertex": vertex})
            except ProtocolError as exc:
                raise GameError(400, str(exc)) from exc
            try:
                new_state, info = self.engine.apply(state, action)
            except IllegalMove as exc:
                raise GameError(400, exc.message) from exc
            self._commit_action(game_id, state, new_state, action, info.captured, None, None)
            return self.get_game(game_id)

    def step(self, game_id: str, think_ms: int | None = None) -> dict:
        with self._game_lock(game_id):
            row = self._row(game_id)
            moves = self.db.list_moves(game_id)
            state, _frames = self._walk(moves)
            if state.finished:
                return self.get_game(game_id)
            seat = self._seat_id(row, state.to_play)
            if seat == "human":
                raise GameError(400, "当前是人类回合")
            agent = self._resolve(seat)
            try:
                action = self.contest.adjudicate(
                    agent,
                    state,
                    self.engine,
                    _payload_moves(moves),
                    think_ms=think_ms,
                )
            except ContestLoss as loss:
                self._commit_forfeit(game_id, state, loss)
                return self.get_game(game_id)
            new_state, info = self.engine.apply(state, action)
            self._commit_action(game_id, state, new_state, action, info.captured, None, think_ms)
            return self.get_game(game_id)

    def analyze(self, game_id: str) -> dict:
        with self._game_lock(game_id):
            self._row(game_id)
            payload = self._write_analysis(game_id)
            return payload

    def sgf(self, game_id: str) -> str:
        from .sgf_export import build_sgf

        game = self.get_game(game_id)
        return build_sgf(game, game.get("analysis"))

    def _commit_action(self, game_id, state, new_state, action: Action, captured, detail, think_ms) -> None:
        vertex = None
        if action.type == "PLACE" and action.x is not None and action.y is not None:
            vertex = to_vertex(action.x, action.y)
        self.db.add_move(
            {
                "game_id": game_id,
                "move_number": state.move_count + 1,
                "color": color_name(state.to_play),
                "action": action.type,
                "x": action.x,
                "y": action.y,
                "vertex": vertex,
                "captured": len(captured),
                "think_ms": think_ms,
            }
        )
        if new_state.finished:
            text = detail or describe_result(new_state)
            self.db.finish_game(game_id, _finish_fields(new_state, text))
            self._write_analysis(game_id)

    def _commit_forfeit(self, game_id: str, state: GameState, loss: ContestLoss) -> None:
        self.db.add_move(
            {
                "game_id": game_id,
                "move_number": state.move_count + 1,
                "color": color_name(state.to_play),
                "action": loss.reason.upper(),
                "x": None,
                "y": None,
                "vertex": None,
                "captured": 0,
                "think_ms": None,
            }
        )
        finished = self.engine.finish_forfeit(state, loss.winner, loss.reason)
        who = "黑方" if state.to_play == BLACK else "白方"
        detail = f"{who}{loss.detail}"
        self.db.finish_game(game_id, _finish_fields(finished, detail))
        self._write_analysis(game_id)

    def _write_analysis(self, game_id: str) -> dict:
        row = self._row(game_id)
        moves = self.db.list_moves(game_id)
        payload = analyze_record(
            moves,
            row["black_name"],
            row["white_name"],
            row["end_reason"],
            row["end_detail"],
        )
        self.db.save_analysis(game_id, json.dumps(payload, ensure_ascii=False))
        return payload

    def _walk(self, move_rows: list[dict]) -> tuple[GameState, list[dict]]:
        state = self.engine.new_game()
        last = None
        frames = [_frame(0, state, None)]
        for row in move_rows:
            state, last = self._apply_row(state, row, last)
            frames.append(_frame(int(row["move_number"]), state, last))
        return state, frames

    def _apply_row(self, state: GameState, row: dict, last):
        action = row["action"]
        if action in ("TIMEOUT", "ILLEGAL", "PROTOCOL"):
            winner = other(color_code(row["color"]))
            return self.engine.finish_forfeit(state, winner, action.lower()), last
        if action == "PLACE":
            new_state, _info = self.engine.apply(state, Action.place(int(row["x"]), int(row["y"])))
            last = {
                "x": row["x"],
                "y": row["y"],
                "vertex": row["vertex"],
                "color": row["color"],
            }
            return new_state, last
        if action == "PASS":
            new_state, _info = self.engine.apply(state, Action.pass_())
            return new_state, last
        if action == "RESIGN":
            new_state, _info = self.engine.apply(state, Action.resign())
            return new_state, last
        raise GameError(500, f"无法回放的动作 {action}")

    def _view(self, row: dict, moves: list[dict], state: GameState, frames: list[dict]) -> dict:
        legal = []
        if not state.finished:
            legal = [
                {"x": x, "y": y, "vertex": to_vertex(x, y)}
                for x, y in self.engine.legal_places(state)
            ]
        analysis = None
        raw = self.db.get_analysis(row["id"])
        if raw:
            analysis = json.loads(raw)
        result = None
        if state.finished:
            scored = state.end_reason == "two_passes" and state.score is not None
            result = {
                "winner": color_name(state.winner),
                "end_reason": state.end_reason,
                "forfeit": state.end_reason != "two_passes",
                "detail": row.get("end_detail") or "",
                "win_stones": state.score.win_stones if scored else None,
                "score": state.score.to_dict() if state.score else None,
            }
        return {
            "id": row["id"],
            "mode": row["mode"],
            "black": {"id": row["black_id"], "name": row["black_name"]},
            "white": {"id": row["white_id"], "name": row["white_name"]},
            "status": "finished" if state.finished else "playing",
            "to_play": None if state.finished else color_name(state.to_play),
            "consecutive_passes": state.consecutive_passes,
            "move_count": state.move_count,
            "board": board_to_rows(state.board),
            "last_move": frames[-1]["last_move"],
            "legal_moves": legal,
            "moves": [_public_move(item) for item in moves],
            "frames": frames,
            "result": result,
            "analysis": analysis,
            "created_at": row["created_at"],
            "finished_at": row["finished_at"],
        }

    def _row(self, game_id: str) -> dict:
        row = self.db.get_game(game_id)
        if row is None:
            raise GameError(404, "对局不存在")
        return row

    def _ensure_player(self, player_id: str) -> None:
        if player_id in DEMO_NAMES:
            status = model_status(player_id)
            if not status["available"]:
                raise GameError(503, status["detail"])
            return
        if player_id == "human" or player_id in BUILTIN_IDS:
            return
        if not self.db.get_agent(player_id):
            raise GameError(400, "没有这个对局者")

    def _player_name(self, player_id: str) -> str:
        if player_id in DEMO_NAMES:
            return DEMO_NAMES[player_id]
        if player_id in BUILTIN_NAMES:
            return BUILTIN_NAMES[player_id]
        agent = self.db.get_agent(player_id)
        return agent["name"] if agent else player_id

    def _resolve(self, player_id: str):
        if player_id in DEMO_NAMES:
            return DemoAgent(player_id)
        factory = BUILTIN_IDS.get(player_id)
        if factory is not None:
            return factory()
        row = self.db.get_agent(player_id)
        if row is None:
            raise GameError(400, "Agent 不存在")
        if row["protocol"] == "stdio":
            return StdioAgent(row["command"])
        if row["protocol"] == "http":
            return HttpAgent(row["endpoint"])
        raise GameError(400, "Agent 协议无法使用")

    def _seat_id(self, row: dict, color: int) -> str:
        if color == BLACK:
            return row["black_id"]
        if color == WHITE:
            return row["white_id"]
        raise GameError(500, "行棋方异常")

    def _game_lock(self, game_id: str) -> threading.RLock:
        with self._lock_guard:
            if game_id not in self._locks:
                self._locks[game_id] = threading.RLock()
            return self._locks[game_id]


def describe_result(state: GameState) -> str:
    if state.end_reason == "two_passes" and state.score is not None:
        score = state.score
        who = "黑方" if state.winner == BLACK else "白方"
        amount = format_number(abs(score.win_stones))
        diff = format_number(score.area_diff_after_komi, 1)
        return (
            f"{who}胜 {amount} 子。黑 {score.black_area}，白 {score.white_area}，"
            f"贴后面积差 {diff} 目，胜子数 = 贴后面积差 ÷ 2。"
        )
    if state.end_reason == "resign":
        who = "黑方" if state.to_play == BLACK else "白方"
        return f"{who}认输"
    return state.end_reason or ""


def _finish_fields(state: GameState, detail: str) -> dict:
    score = state.score
    scored = state.end_reason == "two_passes" and score is not None
    return {
        "winner": color_name(state.winner),
        "end_reason": state.end_reason,
        "end_detail": detail,
        "win_stones": score.win_stones if scored else None,
        "black_area": score.black_area if score else None,
        "white_area": score.white_area if score else None,
        "area_diff_after_komi": score.area_diff_after_komi if score else None,
        "finished_at": utc_now(),
    }


def _mode(black_id: str, white_id: str) -> str:
    humans = (black_id == "human") + (white_id == "human")
    if humans == 2:
        return "human_vs_human"
    if humans == 1:
        return "human_vs_ai"
    return "ai_vs_ai"


def _summary(row: dict) -> dict:
    return {
        "id": row["id"],
        "mode": row["mode"],
        "black": {"id": row["black_id"], "name": row["black_name"]},
        "white": {"id": row["white_id"], "name": row["white_name"]},
        "status": row["status"],
        "winner": row["winner"],
        "end_reason": row["end_reason"],
        "win_stones": row["win_stones"],
        "created_at": row["created_at"],
        "finished_at": row["finished_at"],
    }


def _public_move(row: dict) -> dict:
    return {
        "move_number": row["move_number"],
        "color": row["color"],
        "action": row["action"],
        "x": row["x"],
        "y": row["y"],
        "vertex": row["vertex"],
        "captured": row["captured"],
        "think_ms": row["think_ms"],
    }


def _payload_moves(rows: list[dict]) -> list[dict]:
    payload = []
    for row in rows:
        if row["action"] not in ("PLACE", "PASS", "RESIGN"):
            continue
        payload.append(
            {
                "color": "B" if row["color"] == "black" else "W",
                "action": row["action"],
                "vertex": row["vertex"],
                "x": row["x"],
                "y": row["y"],
            }
        )
    return payload


def _frame(number: int, state: GameState, last) -> dict:
    return {
        "move_number": number,
        "board": board_to_rows(state.board),
        "last_move": last,
        "to_play": None if state.finished else color_name(state.to_play),
        "finished": state.finished,
    }
