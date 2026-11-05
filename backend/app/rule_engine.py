"""九路围棋规则引擎。

落子、提子、禁自杀、全局同形都只在这里判定。
中国数子法：面积 = 子 + 空，提子不重复计入；
黑贴 3¾ 子（7½ 目）；胜子数 = 贴后面积差 ÷ 2。
死子必须实际提掉，计分时不删子。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, replace

BOARD_SIZE = 9
EMPTY = 0
BLACK = 1
WHITE = 2
KOMI_POINTS = 7.5
KOMI_STONES = 3.75

REASON_TEXT = {
    "occupied": "此处已有棋子",
    "suicide": "禁着：自杀",
    "superko": "禁着：全局同形",
    "offboard": "坐标超出棋盘",
    "finished": "对局已结束",
    "protocol": "协议无法识别",
}

RULES = {
    "board_size": BOARD_SIZE,
    "first_player": "black",
    "scoring": "chinese_area",
    "komi_stones": KOMI_STONES,
    "komi_points": KOMI_POINTS,
    "win_stones_formula": "贴后面积差 / 2",
    "prisoners_added_to_area": False,
    "dead_stone_removal": "must_capture",
    "superko": "global_positional",
    "suicide": "forbidden",
    "end": "two_consecutive_passes",
    "ai_actions": ["PLACE", "PASS", "RESIGN"],
    "ai_actions_forbidden": ["END_GAME"],
    "decision_timeout_ms": 10_000,
}

_rng = random.Random(0x9E3779B97F4A7C15)
ZOBRIST_BLACK = tuple(_rng.getrandbits(64) for _ in range(81))
ZOBRIST_WHITE = tuple(_rng.getrandbits(64) for _ in range(81))

NEIGHBORS: list[tuple[int, ...]] = []
for _i in range(81):
    _x, _y = _i % 9, _i // 9
    _near: list[int] = []
    for _dx, _dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        _nx, _ny = _x + _dx, _y + _dy
        if 0 <= _nx < 9 and 0 <= _ny < 9:
            _near.append(_ny * 9 + _nx)
    NEIGHBORS.append(tuple(_near))


class IllegalMove(Exception):
    def __init__(self, code: str, message: str | None = None):
        self.code = code
        self.message = message or REASON_TEXT.get(code, code)
        super().__init__(self.message)


def other(color: int) -> int:
    if color == BLACK:
        return WHITE
    if color == WHITE:
        return BLACK
    raise ValueError(f"未知棋色：{color}")


def color_name(color: int | None) -> str | None:
    if color == BLACK:
        return "black"
    if color == WHITE:
        return "white"
    return None


def color_code(name: str) -> int:
    if name == "black":
        return BLACK
    if name == "white":
        return WHITE
    raise ValueError(name)


@dataclass(frozen=True)
class Action:
    type: str
    x: int | None = None
    y: int | None = None

    @staticmethod
    def place(x: int, y: int) -> Action:
        return Action("PLACE", x, y)

    @staticmethod
    def pass_() -> Action:
        return Action("PASS")

    @staticmethod
    def resign() -> Action:
        return Action("RESIGN")


@dataclass(frozen=True)
class Group:
    color: int
    stones: tuple[int, ...]
    liberties: tuple[int, ...]


@dataclass(frozen=True)
class PlaceDetail:
    ok: bool
    reason: str | None = None
    board: bytes | None = None
    seen: frozenset[int] | None = None
    position_hash: int = 0
    captured: tuple[int, ...] = ()
    liberties: int = 0
    self_atari: bool = False


@dataclass(frozen=True)
class MoveInfo:
    captured: tuple[tuple[int, int], ...]
    self_atari: bool
    liberties_after: int | None


@dataclass(frozen=True)
class Score:
    black_stones: int
    white_stones: int
    black_territory: int
    white_territory: int
    dame: int
    black_area: int
    white_area: int
    komi_points: float
    komi_stones: float
    area_diff_after_komi: float
    win_stones: float
    winner: int | None
    owner: tuple[int, ...]
    prisoners_counted: int = 0

    def to_dict(self) -> dict:
        owner_rows = [list(self.owner[y * 9 : (y + 1) * 9]) for y in range(9)]
        return {
            "black_stones": self.black_stones,
            "white_stones": self.white_stones,
            "black_territory": self.black_territory,
            "white_territory": self.white_territory,
            "dame": self.dame,
            "black_area": self.black_area,
            "white_area": self.white_area,
            "komi_points": self.komi_points,
            "komi_stones": self.komi_stones,
            "area_diff_after_komi": self.area_diff_after_komi,
            "win_stones": self.win_stones,
            "winner": color_name(self.winner),
            "owner": owner_rows,
            "prisoners_counted": self.prisoners_counted,
            "formula": "胜子数 = 贴后面积差 ÷ 2",
        }


@dataclass(frozen=True)
class GameState:
    board: bytes
    to_play: int
    consecutive_passes: int
    seen: frozenset[int]
    position_hash: int
    move_count: int
    finished: bool = False
    winner: int | None = None
    end_reason: str | None = None
    score: Score | None = None


def board_to_rows(board: bytes) -> list[list[int]]:
    return [list(board[y * 9 : (y + 1) * 9]) for y in range(9)]


def rows_to_board(rows: list[list[int]]) -> bytes:
    buf = bytearray(81)
    for y in range(9):
        for x in range(9):
            buf[y * 9 + x] = int(rows[y][x])
    return bytes(buf)


class RuleEngine:
    def hash_board(self, board: bytes) -> int:
        digest = 0
        for index, value in enumerate(board):
            if value == BLACK:
                digest ^= ZOBRIST_BLACK[index]
            elif value == WHITE:
                digest ^= ZOBRIST_WHITE[index]
        return digest

    def new_game(self) -> GameState:
        board = bytes(81)
        digest = self.hash_board(board)
        return GameState(
            board=board,
            to_play=BLACK,
            consecutive_passes=0,
            seen=frozenset((digest,)),
            position_hash=digest,
            move_count=0,
        )

    def from_position(
        self,
        board: bytes,
        to_play: int,
        previous_boards: tuple[bytes, ...] | list[bytes] = (),
        consecutive_passes: int = 0,
    ) -> GameState:
        if len(board) != 81 or any(value not in (EMPTY, BLACK, WHITE) for value in board):
            raise ValueError("棋盘必须是 9×9，棋子只能是空、黑、白")
        if to_play not in (BLACK, WHITE):
            raise ValueError("行棋方只能是黑或白")
        hashes = [self.hash_board(bytes(item)) for item in previous_boards]
        current = bytes(board)
        digest = self.hash_board(current)
        if digest not in hashes:
            hashes.append(digest)
        return GameState(
            board=current,
            to_play=to_play,
            consecutive_passes=consecutive_passes,
            seen=frozenset(hashes),
            position_hash=digest,
            move_count=0,
        )

    def _group(self, board, start: int) -> tuple[list[int], set[int]]:
        color = board[start]
        stones: list[int] = []
        liberties: set[int] = set()
        stack = [start]
        visited = {start}
        while stack:
            point = stack.pop()
            stones.append(point)
            for near in NEIGHBORS[point]:
                value = board[near]
                if value == EMPTY:
                    liberties.add(near)
                elif value == color and near not in visited:
                    visited.add(near)
                    stack.append(near)
        return stones, liberties

    def all_groups(self, board: bytes) -> list[Group]:
        seen = bytearray(81)
        groups: list[Group] = []
        for start in range(81):
            if board[start] == EMPTY or seen[start]:
                continue
            stones, liberties = self._group(board, start)
            for stone in stones:
                seen[stone] = 1
            groups.append(
                Group(
                    color=board[start],
                    stones=tuple(stones),
                    liberties=tuple(liberties),
                )
            )
        return groups

    def place_detail(
        self,
        board: bytes,
        x: int,
        y: int,
        color: int,
        seen: frozenset[int],
    ) -> PlaceDetail:
        if not (0 <= x < 9 and 0 <= y < 9):
            return PlaceDetail(False, "offboard")
        index = y * 9 + x
        if board[index] != EMPTY:
            return PlaceDetail(False, "occupied")
        buf = bytearray(board)
        buf[index] = color
        opponent = other(color)
        captured: list[int] = []
        captured_set: set[int] = set()
        for near in NEIGHBORS[index]:
            if buf[near] != opponent or near in captured_set:
                continue
            stones, liberties = self._group(buf, near)
            if liberties:
                continue
            for stone in stones:
                if stone in captured_set:
                    continue
                captured_set.add(stone)
                buf[stone] = EMPTY
                captured.append(stone)
        _own_stones, own_liberties = self._group(buf, index)
        if not own_liberties:
            return PlaceDetail(False, "suicide")
        new_board = bytes(buf)
        digest = self.hash_board(new_board)
        if digest in seen:
            return PlaceDetail(False, "superko")
        return PlaceDetail(
            ok=True,
            board=new_board,
            seen=seen | {digest},
            position_hash=digest,
            captured=tuple(captured),
            liberties=len(own_liberties),
            self_atari=len(own_liberties) == 1,
        )

    def legal_places(self, state: GameState) -> list[tuple[int, int]]:
        if state.finished:
            return []
        found: list[tuple[int, int]] = []
        for index in range(81):
            if state.board[index] != EMPTY:
                continue
            x, y = index % 9, index // 9
            if self.place_detail(state.board, x, y, state.to_play, state.seen).ok:
                found.append((x, y))
        return found

    def score_board(self, board: bytes) -> Score:
        """数子。空点只在整块只贴到一种棋时计入该方，不把提子再加一次。"""
        seen = bytearray(81)
        owner = [0] * 81
        black_territory = 0
        white_territory = 0
        dame = 0
        for start in range(81):
            if board[start] != EMPTY or seen[start]:
                continue
            stack = [start]
            seen[start] = 1
            region = [start]
            borders = 0
            while stack:
                point = stack.pop()
                for near in NEIGHBORS[point]:
                    value = board[near]
                    if value == EMPTY and not seen[near]:
                        seen[near] = 1
                        stack.append(near)
                        region.append(near)
                    elif value == BLACK:
                        borders |= 1
                    elif value == WHITE:
                        borders |= 2
            if borders == 1:
                mark = BLACK
                black_territory += len(region)
            elif borders == 2:
                mark = WHITE
                white_territory += len(region)
            else:
                mark = 3
                dame += len(region)
            for point in region:
                owner[point] = mark
        black_stones = sum(1 for value in board if value == BLACK)
        white_stones = sum(1 for value in board if value == WHITE)
        black_area = black_stones + black_territory
        white_area = white_stones + white_territory
        if black_stones + white_stones + black_territory + white_territory + dame != 81:
            raise RuntimeError("面积划分没有盖住棋盘")
        area_diff = black_area - white_area - KOMI_POINTS
        win_stones = area_diff / 2
        if win_stones > 0:
            winner: int | None = BLACK
        elif win_stones < 0:
            winner = WHITE
        else:
            winner = None
        return Score(
            black_stones=black_stones,
            white_stones=white_stones,
            black_territory=black_territory,
            white_territory=white_territory,
            dame=dame,
            black_area=black_area,
            white_area=white_area,
            komi_points=KOMI_POINTS,
            komi_stones=KOMI_STONES,
            area_diff_after_komi=area_diff,
            win_stones=win_stones,
            winner=winner,
            owner=tuple(owner),
            prisoners_counted=0,
        )

    def try_apply(self, state: GameState, action: Action) -> tuple[GameState, MoveInfo] | None:
        if state.finished or action.type not in ("PLACE", "PASS", "RESIGN"):
            return None
        if action.type == "PASS":
            return self._pass(state), MoveInfo((), False, None)
        if action.type == "RESIGN":
            return self._resign(state), MoveInfo((), False, None)
        if action.x is None or action.y is None:
            return None
        detail = self.place_detail(state.board, action.x, action.y, state.to_play, state.seen)
        if not detail.ok or detail.board is None or detail.seen is None:
            return None
        new_state = GameState(
            board=detail.board,
            to_play=other(state.to_play),
            consecutive_passes=0,
            seen=detail.seen,
            position_hash=detail.position_hash,
            move_count=state.move_count + 1,
        )
        captured = tuple((point % 9, point // 9) for point in detail.captured)
        return new_state, MoveInfo(captured, detail.self_atari, detail.liberties)

    def apply(self, state: GameState, action: Action) -> tuple[GameState, MoveInfo]:
        result = self.try_apply(state, action)
        if result is not None:
            return result
        if state.finished:
            raise IllegalMove("finished")
        if action.type == "PLACE" and action.x is not None and action.y is not None:
            detail = self.place_detail(state.board, action.x, action.y, state.to_play, state.seen)
            raise IllegalMove(detail.reason or "protocol")
        raise IllegalMove("protocol")

    def _pass(self, state: GameState) -> GameState:
        passes = state.consecutive_passes + 1
        if passes >= 2:
            score = self.score_board(state.board)
            return GameState(
                board=state.board,
                to_play=other(state.to_play),
                consecutive_passes=passes,
                seen=state.seen,
                position_hash=state.position_hash,
                move_count=state.move_count + 1,
                finished=True,
                winner=score.winner,
                end_reason="two_passes",
                score=score,
            )
        return GameState(
            board=state.board,
            to_play=other(state.to_play),
            consecutive_passes=passes,
            seen=state.seen,
            position_hash=state.position_hash,
            move_count=state.move_count + 1,
        )

    def _resign(self, state: GameState) -> GameState:
        return GameState(
            board=state.board,
            to_play=state.to_play,
            consecutive_passes=state.consecutive_passes,
            seen=state.seen,
            position_hash=state.position_hash,
            move_count=state.move_count + 1,
            finished=True,
            winner=other(state.to_play),
            end_reason="resign",
            score=self.score_board(state.board),
        )

    def finish_forfeit(self, state: GameState, winner: int, reason: str) -> GameState:
        return replace(
            state,
            finished=True,
            winner=winner,
            end_reason=reason,
            score=state.score or self.score_board(state.board),
        )
