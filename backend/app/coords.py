"""9×9 坐标：内部 x、y 从左下角起算，GTP 列名跳过 I。"""

from __future__ import annotations

COLS = "ABCDEFGHJ"


def to_vertex(x: int, y: int) -> str:
    if not (0 <= x < 9 and 0 <= y < 9):
        raise ValueError(f"坐标超出棋盘：{(x, y)}")
    return f"{COLS[x]}{y + 1}"


def parse_vertex(text: str) -> tuple[int, int]:
    raw = text.strip().upper()
    if len(raw) < 2 or raw[0] not in COLS:
        raise ValueError(raw)
    try:
        row = int(raw[1:])
    except ValueError as exc:
        raise ValueError(raw) from exc
    if not 1 <= row <= 9:
        raise ValueError(raw)
    return COLS.index(raw[0]), row - 1


def to_sgf(x: int, y: int) -> str:
    """SGF 坐标从左上角起算，9 路用 a–i。"""
    if not (0 <= x < 9 and 0 <= y < 9):
        raise ValueError(f"坐标超出棋盘：{(x, y)}")
    return chr(ord("a") + x) + chr(ord("a") + (8 - y))
