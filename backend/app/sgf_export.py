"""棋谱导出为 SGF。"""

from __future__ import annotations

from .coords import to_sgf


def build_sgf(game: dict, analysis: dict | None = None) -> str:
    notes = {}
    if analysis:
        for item in analysis.get("move_notes") or []:
            notes[int(item["move_number"])] = item
    black = _escape(game["black"]["name"])
    white = _escape(game["white"]["name"])
    root_bits = [
        "FF[4]",
        "CA[UTF-8]",
        "GM[1]",
        "SZ[9]",
        "AP[Xiaqi:1]",
        "KM[7.5]",
        "RU[Chinese]",
        f"PB[{black}]",
        f"PW[{white}]",
    ]
    result = game.get("result")
    if result and result.get("winner"):
        root_bits.append(f"RE[{_result_tag(result)}]")
    comment = _root_comment(game, analysis)
    root_bits.append(f"GC[{_escape(comment)}]")
    nodes = [";" + "".join(root_bits)]
    for move in game.get("moves") or []:
        if move["action"] not in ("PLACE", "PASS"):
            continue
        color = "B" if move["color"] == "black" else "W"
        coord = ""
        if move["action"] == "PLACE":
            coord = to_sgf(int(move["x"]), int(move["y"]))
        body = f";{color}[{coord}]"
        comment_parts: list[str] = []
        if move.get("captured"):
            comment_parts.append(f"提{move['captured']}子")
        note = notes.get(int(move["move_number"]))
        if note:
            comment_parts.extend(note.get("notes") or [])
            if note.get("suggestion"):
                comment_parts.append(f"参考着 {note['suggestion']}")
        if comment_parts:
            body += f"C[{_escape('；'.join(comment_parts))}]"
        nodes.append(body)
    return "(" + "".join(nodes) + ")\n"


def _result_tag(result: dict) -> str:
    winner = "B" if result.get("winner") == "black" else "W"
    reason = result.get("end_reason")
    if reason == "two_passes" and result.get("win_stones") is not None:
        return f"{winner}+{abs(float(result['win_stones'])):.2f}"
    if reason == "resign":
        return f"{winner}+R"
    if reason == "timeout":
        return f"{winner}+T"
    return f"{winner}+F"


def _root_comment(game: dict, analysis: dict | None) -> str:
    lines = [
        "中国数子法，9×9，黑先。黑贴 3¾ 子（7½ 目）。",
        "胜子数 = 贴后面积差 ÷ 2。提子不重复加入面积。",
        "死活须实际提净，不自动删子。双方连续两次 PASS 终局。",
    ]
    result = game.get("result") or {}
    if result.get("detail"):
        lines.append(str(result["detail"]))
    if analysis and analysis.get("summary"):
        lines.append(str(analysis["summary"]))
    return "\n".join(lines)


def _escape(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace("]", "\\]")
