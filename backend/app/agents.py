"""内置 AI 与外部 Agent。落子合法性只调用 RuleEngine。"""

from __future__ import annotations

import json
import math
import os
import random
import shlex
import subprocess
import time

import httpx

from .config import ROOT
from .evaluate import fast_policy, one_ply
from .protocol import DecisionTimeout, ProtocolError
from .rule_engine import WHITE, Action, GameState, IllegalMove, RuleEngine, other


class RandomAgent:
    is_builtin = True
    name = "随机"

    def __init__(self, seed: int | None = None):
        self.engine = RuleEngine()
        self.rng = random.Random(seed)

    def decide(self, state: GameState, budget_ms: int) -> Action:
        del budget_ms
        places = self.engine.legal_places(state)
        if not places:
            return Action.pass_()
        if state.move_count > 50 and self.rng.random() < 0.08:
            return Action.pass_()
        x, y = places[self.rng.randrange(len(places))]
        return Action.place(x, y)


class HeuristicAgent:
    is_builtin = True
    name = "启发式"

    def __init__(self):
        self.engine = RuleEngine()

    def decide(self, state: GameState, budget_ms: int) -> Action:
        del budget_ms
        picked = one_ply(self.engine, state)
        if picked.best is None or picked.pass_value > picked.best_value:
            return Action.pass_()
        return Action.place(*picked.best)


class _Child:
    __slots__ = ("state", "action", "visits", "black_wins")

    def __init__(self, state: GameState, action: Action):
        self.state = state
        self.action = action
        self.visits = 0
        self.black_wins = 0.0


class MCTSAgent:
    """一层蒙特卡洛。每个模拟着法都经 RuleEngine 落地。"""

    is_builtin = True
    name = "蒙特卡洛"

    def __init__(self, seed: int = 0):
        self.engine = RuleEngine()
        self.rng = random.Random(seed)

    def decide(self, state: GameState, budget_ms: int) -> Action:
        if state.finished:
            return Action.pass_()
        actions = self._candidates(state)
        children: list[_Child] = []
        for action in actions:
            try:
                new_state, _info = self.engine.apply(state, action)
            except IllegalMove:
                continue
            children.append(_Child(new_state, action))
        if not children:
            return Action.pass_()
        deadline = time.perf_counter() + max(0.05, budget_ms / 1000)
        sims = 0
        while time.perf_counter() < deadline and sims < 6000:
            child = self._select(children)
            child.black_wins += self._playout(child.state, deadline)
            child.visits += 1
            sims += 1
        best = max(children, key=lambda item: item.visits)
        return best.action

    def _candidates(self, state: GameState) -> list[Action]:
        places = self.engine.legal_places(state)
        if not places:
            return [Action.pass_()]
        groups = self.engine.all_groups(state.board)
        capture: set[int] = set()
        save: set[int] = set()
        opponent = other(state.to_play)
        for group in groups:
            if len(group.liberties) != 1:
                continue
            liberty = group.liberties[0]
            if group.color == opponent:
                capture.add(liberty)
            else:
                save.add(liberty)

        def rank(point: tuple[int, int]) -> int:
            index = point[1] * 9 + point[0]
            score = 0
            if index in capture:
                score += 120
            if index in save:
                score += 90
            score -= (point[0] - 4) ** 2 + (point[1] - 4) ** 2
            return score

        places.sort(key=rank, reverse=True)
        urgent = [point for point in places if (point[1] * 9 + point[0]) in capture or (point[1] * 9 + point[0]) in save]
        rest = [point for point in places if point not in urgent]
        chosen = urgent + rest[: max(0, 14 - len(urgent))]
        actions = [Action.place(x, y) for x, y in chosen]
        if state.move_count >= 16 or len(places) <= len(chosen):
            actions.append(Action.pass_())
        return actions

    def _select(self, children: list[_Child]) -> _Child:
        for child in children:
            if child.visits == 0:
                return child
        total = sum(child.visits for child in children)
        log_n = math.log(total + 1)
        # 子节点里的 to_play 是落子之后的一方；白方轮走说明刚刚是黑棋下的。
        black_just_moved = children[0].state.to_play == WHITE

        def score(child: _Child) -> float:
            rate = child.black_wins / child.visits
            if not black_just_moved:
                rate = 1 - rate
            return rate + 1.3 * math.sqrt(log_n / child.visits)

        return max(children, key=score)

    def _playout(self, state: GameState, deadline: float) -> float:
        guard = 100
        while not state.finished and guard > 0:
            if time.perf_counter() > deadline:
                break
            action = fast_policy(self.engine, state, self.rng)
            try:
                state, _info = self.engine.apply(state, action)
            except IllegalMove:
                state, _info = self.engine.apply(state, Action.pass_())
            guard -= 1
        return self._black_win(state)

    def _black_win(self, state: GameState) -> float:
        if state.finished and state.winner == BLACK:
            return 1.0
        if state.finished and state.winner == WHITE:
            return 0.0
        score = state.score or self.engine.score_board(state.board)
        if score.win_stones > 0:
            return 1.0
        if score.win_stones < 0:
            return 0.0
        return 0.5


def split_command(command: str) -> list[str]:
    """Windows 的非 POSIX 拆分会把引号留在参数里，路径会因此找不到。"""
    if os.name == "nt":
        return [part.strip('"') for part in shlex.split(command, posix=False) if part.strip('"')]
    return shlex.split(command, posix=True)


class StdioAgent:
    is_builtin = False

    def __init__(self, command: str):
        self.command = command

    def raw_decision(self, payload: dict, timeout_s: float) -> dict:
        try:
            run_kwargs = {}
            if os.name == "nt":
                run_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            completed = subprocess.run(
                split_command(self.command),
                input=json.dumps(payload),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=timeout_s,
                cwd=str(ROOT),
                check=False,
                **run_kwargs,
            )
        except subprocess.TimeoutExpired as exc:
            raise DecisionTimeout() from exc
        except FileNotFoundError as exc:
            raise ProtocolError(f"无法启动 Agent：{exc}") from exc
        except ValueError as exc:
            raise ProtocolError(f"命令无法解析：{exc}") from exc
        line = ""
        for candidate in reversed(completed.stdout.splitlines()):
            if candidate.strip():
                line = candidate.strip()
                break
        if not line:
            err = (completed.stderr or "").strip()[:240]
            raise ProtocolError(f"Agent 没有输出 {err}".strip())
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ProtocolError("Agent 输出不是 JSON") from exc
        return data


class HttpAgent:
    is_builtin = False

    def __init__(self, endpoint: str):
        self.endpoint = endpoint

    def raw_decision(self, payload: dict, timeout_s: float) -> dict:
        try:
            with httpx.Client(timeout=timeout_s) as client:
                response = client.post(self.endpoint, json=payload)
        except httpx.TimeoutException as exc:
            raise DecisionTimeout() from exc
        except httpx.HTTPError as exc:
            raise ProtocolError(f"Agent 请求失败：{exc}") from exc
        if response.status_code != 200:
            raise ProtocolError(f"Agent HTTP {response.status_code}")
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise ProtocolError("Agent 输出不是 JSON") from exc


BUILTIN_IDS = {
    "builtin:random": RandomAgent,
    "builtin:heuristic": HeuristicAgent,
    "builtin:mcts": MCTSAgent,
}

BUILTIN_NAMES = {
    "human": "人类",
    "builtin:random": "随机",
    "builtin:heuristic": "启发式",
    "builtin:mcts": "蒙特卡洛",
}
