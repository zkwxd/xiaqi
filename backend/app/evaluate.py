"""启发式估值。正式胜负只走 RuleEngine.score_board，这里只给着法排序和复盘。"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .rule_engine import (
    BLACK,
    EMPTY,
    Action,
    GameState,
    RuleEngine,
    other,
)


@dataclass(frozen=True)
class OnePly:
    best: tuple[int, int] | None
    best_value: float
    pass_value: float
    values: dict[tuple[int, int], float]


def influence(board: bytes) -> float:
    """黑方为正的粗略厚势，不是终局数子。"""
    acc = [0.0] * 81
    for index, value in enumerate(board):
        if value == EMPTY:
            continue
        sign = 1.0 if value == BLACK else -1.0
        x, y = index % 9, index // 9
        for ny in range(max(0, y - 3), min(9, y + 4)):
            for nx in range(max(0, x - 3), min(9, x + 4)):
                distance = abs(nx - x) + abs(ny - y)
                if distance == 0 or distance > 3:
                    continue
                acc[ny * 9 + nx] += sign / distance
    score = 0.0
    for index, value in enumerate(board):
        if value == BLACK:
            score += 1.0
        elif value != EMPTY:
            score -= 1.0
        elif acc[index] > 0.2:
            score += 0.45
        elif acc[index] < -0.2:
            score -= 0.45
    return score


def one_ply(engine: RuleEngine, state: GameState) -> OnePly:
    base = engine.score_board(state.board)
    sign = 1.0 if state.to_play == BLACK else -1.0
    color = state.to_play
    opponent = other(color)
    best: tuple[int, int] | None = None
    best_value = float("-inf")
    values: dict[tuple[int, int], float] = {}
    for index in range(81):
        if state.board[index] != EMPTY:
            continue
        x, y = index % 9, index // 9
        detail = engine.place_detail(state.board, x, y, color, state.seen)
        if not detail.ok or detail.board is None:
            continue
        value = influence(detail.board) * sign
        value += len(detail.captured) * 2.2
        if detail.self_atari and not detail.captured:
            value -= 2.4
        elif detail.self_atari:
            value -= 0.6
        owner = base.owner[index]
        if owner == color:
            value -= 2.6
        elif owner == opponent:
            value += 1.1
        elif owner == 3:
            value += 0.35
        center = abs(x - 4) + abs(y - 4)
        value -= center * 0.03
        values[(x, y)] = value
        if value > best_value:
            best_value = value
            best = (x, y)
    pass_value = influence(state.board) * sign - 1.35
    return OnePly(best, best_value, pass_value, values)


def fast_policy(engine: RuleEngine, state: GameState, rng: random.Random) -> Action:
    """模拟用的快着。合法性仍交给 RuleEngine。"""
    if state.finished:
        return Action.pass_()
    groups = engine.all_groups(state.board)
    urgent: list[int] = []
    for group in groups:
        if len(group.liberties) == 1:
            urgent.append(group.liberties[0])
    rng.shuffle(urgent)
    tried: set[int] = set()
    for point in urgent:
        if point in tried:
            continue
        tried.add(point)
        x, y = point % 9, point // 9
        if engine.place_detail(state.board, x, y, state.to_play, state.seen).ok:
            return Action.place(x, y)
    empties = [index for index, value in enumerate(state.board) if value == EMPTY]
    rng.shuffle(empties)
    for point in empties[:16]:
        x, y = point % 9, point // 9
        if engine.place_detail(state.board, x, y, state.to_play, state.seen).ok:
            return Action.place(x, y)
    return Action.pass_()
