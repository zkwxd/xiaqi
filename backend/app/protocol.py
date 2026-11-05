"""AI 协议与竞赛外层。

开放 PLACE / PASS / RESIGN。END_GAME 一律协议判负。
每次决策硬超时 10 秒；超时、非法落子、协议错误由这里判负。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

from .config import DECISION_TIMEOUT_MS
from .coords import parse_vertex, to_vertex
from .rule_engine import (
    BLACK,
    Action,
    GameState,
    IllegalMove,
    RuleEngine,
    board_to_rows,
    other,
)


class ProtocolError(Exception):
    pass


class DecisionTimeout(Exception):
    pass


class ContestLoss(Exception):
    def __init__(self, winner: int, reason: str, detail: str):
        super().__init__(detail)
        self.winner = winner
        self.reason = reason
        self.detail = detail


def parse_decision(data: object) -> Action:
    if not isinstance(data, dict):
        raise ProtocolError("决策必须是 JSON 对象")
    action = str(data.get("action", "")).strip().upper()
    if action == "END_GAME":
        raise ProtocolError("协议未开放 END_GAME")
    if action == "PASS":
        return Action.pass_()
    if action == "RESIGN":
        return Action.resign()
    if action != "PLACE":
        raise ProtocolError(f"无法识别的动作：{action or '空'}")
    vertex = data.get("vertex")
    if vertex:
        try:
            x, y = parse_vertex(str(vertex))
        except ValueError as exc:
            raise ProtocolError(f"坐标无法解析：{vertex}") from exc
        return Action.place(x, y)
    if "x" not in data or "y" not in data:
        raise ProtocolError("PLACE 需要 vertex 或 x、y")
    try:
        x = int(data["x"])
        y = int(data["y"])
    except (TypeError, ValueError) as exc:
        raise ProtocolError("x、y 必须是整数") from exc
    if not (0 <= x < 9 and 0 <= y < 9):
        raise ProtocolError("坐标超出 9×9 棋盘")
    return Action.place(x, y)


def build_payload(
    state: GameState,
    engine: RuleEngine,
    moves: list[dict] | None = None,
    time_limit_ms: int = DECISION_TIMEOUT_MS,
) -> dict:
    if state.finished:
        vertices: list[str] = []
    else:
        vertices = [to_vertex(x, y) for x, y in engine.legal_places(state)]
    return {
        "protocol": "xiaqi-1",
        "board_size": 9,
        "to_play": "B" if state.to_play == BLACK else "W",
        "komi_points": 7.5,
        "komi_stones": 3.75,
        "consecutive_passes": state.consecutive_passes,
        "move_count": state.move_count,
        "board": board_to_rows(state.board),
        "moves": moves or [],
        "legal_vertices": vertices,
        "allowed_actions": ["PLACE", "PASS", "RESIGN"],
        "time_limit_ms": time_limit_ms,
        "rules": {
            "scoring": "chinese_area",
            "prisoners_added_to_area": False,
            "superko": "global_positional",
            "suicide": "forbidden",
            "end": "two_consecutive_passes",
            "dead_stones": "must_be_captured",
        },
    }


def builtin_budget(think_ms: int | None) -> int:
    if think_ms is None:
        think_ms = 2000
    return max(200, min(int(think_ms), 9_500))


class ContestLayer:
    """竞赛外层。内置 AI 的思考预算可以短于 10 秒，判负时限始终是 10 秒。"""

    TIMEOUT_MS = DECISION_TIMEOUT_MS

    def adjudicate(
        self,
        agent: object,
        state: GameState,
        engine: RuleEngine,
        moves: list[dict] | None = None,
        think_ms: int | None = None,
        timeout_ms: int | None = None,
    ) -> Action:
        limit = self.TIMEOUT_MS if timeout_ms is None else int(timeout_ms)
        try:
            action = self._request(agent, state, engine, moves, think_ms, limit)
            self._validate(action, state, engine)
            return action
        except DecisionTimeout as exc:
            raise ContestLoss(other(state.to_play), "timeout", f"决策超过 {limit} ms") from exc
        except ProtocolError as exc:
            raise ContestLoss(other(state.to_play), "protocol", str(exc)) from exc
        except IllegalMove as exc:
            raise ContestLoss(other(state.to_play), "illegal", exc.message) from exc

    def _request(
        self,
        agent: object,
        state: GameState,
        engine: RuleEngine,
        moves: list[dict] | None,
        think_ms: int | None,
        timeout_ms: int,
    ) -> Action:
        if getattr(agent, "is_builtin", False):
            budget = min(builtin_budget(think_ms), max(1, timeout_ms))
            return self._call_builtin(agent, state, budget, timeout_ms)
        payload = build_payload(state, engine, moves, timeout_ms)
        data = agent.raw_decision(payload, timeout_ms / 1000)  # type: ignore[attr-defined]
        return parse_decision(data)

    def _call_builtin(self, agent: object, state: GameState, budget_ms: int, timeout_ms: int) -> Action:
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="xiaqi-agent")
        future = pool.submit(agent.decide, state, budget_ms)  # type: ignore[attr-defined]
        try:
            action = future.result(timeout=timeout_ms / 1000)
        except FuturesTimeoutError as exc:
            pool.shutdown(wait=False, cancel_futures=True)
            raise DecisionTimeout() from exc
        except (ProtocolError, IllegalMove, DecisionTimeout):
            pool.shutdown(wait=False)
            raise
        except Exception as exc:
            pool.shutdown(wait=False)
            raise ProtocolError(f"Agent 内部错误：{exc}") from exc
        else:
            pool.shutdown(wait=False)
        if not isinstance(action, Action):
            raise ProtocolError("内置 Agent 没有返回动作")
        return action

    def _validate(self, action: Action, state: GameState, engine: RuleEngine) -> None:
        if not isinstance(action, Action) or action.type not in ("PLACE", "PASS", "RESIGN"):
            name = getattr(action, "type", "")
            if str(name).upper() == "END_GAME":
                raise ProtocolError("协议未开放 END_GAME")
            raise ProtocolError("动作必须是 PLACE、PASS 或 RESIGN")
        if state.finished:
            raise IllegalMove("finished")
        if action.type != "PLACE":
            return
        if action.x is None or action.y is None:
            raise ProtocolError("PLACE 缺少坐标")
        detail = engine.place_detail(state.board, action.x, action.y, state.to_play, state.seen)
        if not detail.ok:
            raise IllegalMove(detail.reason or "protocol")
