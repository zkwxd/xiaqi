"""终局复盘。胜负面积来自 RuleEngine，缺点来自可验证的吃子、打吃和着法差。"""

from __future__ import annotations

from .coords import to_vertex
from .evaluate import one_ply
from .rule_engine import BLACK, WHITE, Action, RuleEngine, color_name, other

_RANK = {"normal": 0, "inaccuracy": 1, "mistake": 2, "blunder": 3}


def format_number(value: float, digits: int = 2) -> str:
    text = f"{value:.{digits}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _bump(current: str, new: str) -> str:
    return new if _RANK[new] > _RANK[current] else current


class _Bag:
    def __init__(self):
        self.missed: list[int] = []
        self.atari: list[int] = []
        self.tight: list[int] = []
        self.filled: list[int] = []
        self.early: list[int] = []
        self.regret: list[int] = []
        self.lost = 0


def analyze_record(
    move_rows: list[dict],
    black_name: str,
    white_name: str,
    end_reason: str | None,
    end_detail: str | None,
) -> dict:
    engine = RuleEngine()
    state = engine.new_game()
    bags = {BLACK: _Bag(), WHITE: _Bag()}
    notes: list[dict] = []
    for row in move_rows:
        if state.finished:
            break
        number = int(row["move_number"])
        color = BLACK if row["color"] == "black" else WHITE
        action_name = row["action"]
        if action_name in ("TIMEOUT", "ILLEGAL", "PROTOCOL"):
            notes.append(
                {
                    "move_number": number,
                    "color": row["color"],
                    "severity": "blunder",
                    "notes": [_forfeit_note(action_name, end_detail)],
                    "suggestion": None,
                }
            )
            bags[color].regret.append(number)
            state = engine.finish_forfeit(state, other(color), action_name.lower())
            break
        action = _action_from_row(row)
        severity = "normal"
        comments: list[str] = []
        suggestion = None
        if action.type in ("PLACE", "PASS"):
            ply = one_ply(engine, state)
            groups = engine.all_groups(state.board)
            captures = _capture_options(engine, state, groups)
            saves = _save_options(engine, state, groups)
            played = (action.x, action.y) if action.type == "PLACE" else None
            capture_at = {item[0] for item in captures}
            save_at = {item[0] for item in saves}
            if captures and played not in capture_at:
                size = max(item[1] for item in captures)
                point = max(captures, key=lambda item: item[1])[0]
                if size >= 2:
                    severity = _bump(severity, "mistake")
                    bags[color].missed.append(number)
                    comments.append(f"放过提子，{to_vertex(*point)} 可提 {size} 子")
                elif size == 1 and not any(item[2] for item in captures):
                    severity = _bump(severity, "inaccuracy")
                    bags[color].missed.append(number)
                    comments.append(f"可在 {to_vertex(*point)} 提子，这一手没走")
            if saves and played not in save_at and played not in capture_at:
                size = max(item[1] for item in saves)
                point = max(saves, key=lambda item: item[1])[0]
                level = "blunder" if size >= 4 else "mistake"
                severity = _bump(severity, level)
                bags[color].atari.append(number)
                comments.append(f"{size} 子被打吃，没有走 {to_vertex(*point)} 或反提")
            if action.type == "PASS":
                valuable = [
                    point
                    for point, value in ply.values.items()
                    if value > ply.pass_value
                ]
                if len(valuable) >= 3:
                    severity = _bump(severity, "mistake")
                    bags[color].early.append(number)
                    comments.append("还有可占的点，却选择停着")
                if ply.best is not None and ply.best_value > ply.pass_value + 4:
                    suggestion = to_vertex(*ply.best)
            else:
                owner = engine.score_board(state.board).owner[action.y * 9 + action.x]
                if owner == color:
                    severity = _bump(severity, "inaccuracy")
                    bags[color].filled.append(number)
                    comments.append("落在自己的空里，数子法下容易亏气")
                regret = ply.best_value - ply.values.get((action.x, action.y), ply.best_value)
                if regret >= 14:
                    severity = _bump(severity, "blunder")
                    bags[color].regret.append(number)
                    comments.append("这一手和参考着差得比较远")
                elif regret >= 8:
                    severity = _bump(severity, "mistake")
                    bags[color].regret.append(number)
                    comments.append("静态判断明显低于参考着")
                elif regret >= 4:
                    severity = _bump(severity, "inaccuracy")
                    bags[color].regret.append(number)
                    comments.append("还有更厚的点")
                if ply.best is not None and ply.best != (action.x, action.y) and severity != "normal":
                    suggestion = to_vertex(*ply.best)
        new_state, info = engine.apply(state, action)
        if info.self_atari and not info.captured and action.type == "PLACE":
            severity = _bump(severity, "inaccuracy")
            bags[color].tight.append(number)
            comments.append("落子之后只剩一口气")
        if info.captured:
            bags[other(color)].lost += len(info.captured)
            comments.append(f"提掉 {len(info.captured)} 子")
        notes.append(
            {
                "move_number": number,
                "color": row["color"],
                "severity": severity,
                "notes": comments,
                "suggestion": suggestion,
            }
        )
        state = new_state

    names = {"black": black_name, "white": white_name}
    weaknesses = _weaknesses(bags, names, end_reason, end_detail)
    summary = _summary(state, names, end_reason, end_detail, weaknesses)
    return {
        "summary": summary,
        "advice": _advice(weaknesses),
        "weaknesses": weaknesses,
        "move_notes": notes,
        "score": state.score.to_dict() if state.score else None,
    }


def _action_from_row(row: dict) -> Action:
    if row["action"] == "PASS":
        return Action.pass_()
    if row["action"] == "RESIGN":
        return Action.resign()
    return Action.place(int(row["x"]), int(row["y"]))


def _capture_options(engine: RuleEngine, state, groups) -> list[tuple[tuple[int, int], int, bool]]:
    found = []
    opponent = other(state.to_play)
    for group in groups:
        if group.color != opponent or len(group.liberties) != 1:
            continue
        point = group.liberties[0]
        x, y = point % 9, point // 9
        detail = engine.place_detail(state.board, x, y, state.to_play, state.seen)
        if detail.ok and detail.captured and not detail.self_atari:
            found.append(((x, y), len(detail.captured), detail.self_atari))
    return found


def _save_options(engine: RuleEngine, state, groups) -> list[tuple[tuple[int, int], int]]:
    found = []
    for group in groups:
        if group.color != state.to_play or len(group.liberties) != 1 or len(group.stones) < 2:
            continue
        point = group.liberties[0]
        x, y = point % 9, point // 9
        detail = engine.place_detail(state.board, x, y, state.to_play, state.seen)
        if detail.ok and not detail.self_atari:
            found.append(((x, y), len(group.stones)))
    return found


def _forfeit_note(action_name: str, detail: str | None) -> str:
    label = {"TIMEOUT": "决策超时", "ILLEGAL": "非法落子", "PROTOCOL": "协议错误"}[action_name]
    text = f"竞赛外层判负：{label}"
    if detail:
        text += f"（{detail}）"
    return text


def _weaknesses(bags: dict[int, _Bag], names: dict[str, str], end_reason: str | None, end_detail: str | None) -> list[dict]:
    items: list[dict] = []
    if end_reason in ("timeout", "illegal", "protocol"):
        items.append(
            {
                "player": "both",
                "title": "竞赛判负",
                "detail": end_detail or _forfeit_note(end_reason.upper(), None),
                "moves": [],
            }
        )
    for color in (BLACK, WHITE):
        name = names[color_name(color)]
        bag = bags[color]
        side = color_name(color)
        if bag.missed:
            items.append(
                {
                    "player": side,
                    "title": "吃子偏慢",
                    "detail": f"{name}有 {len(bag.missed)} 手放过了可以立刻提掉的棋。九路上提子会直接从对方面积里消失。",
                    "moves": bag.missed,
                }
            )
        if bag.atari:
            items.append(
                {
                    "player": side,
                    "title": "治孤不及时",
                    "detail": f"{name}有 {len(bag.atari)} 手在大棋被打吃时没有应。死子不会在终局被自动拿掉，要自己提净。",
                    "moves": bag.atari,
                }
            )
        if len(bag.tight) >= 2:
            items.append(
                {
                    "player": side,
                    "title": "自紧气",
                    "detail": f"{name}有 {len(bag.tight)} 手把自己走到只剩一口气。",
                    "moves": bag.tight,
                }
            )
        if bag.filled:
            items.append(
                {
                    "player": side,
                    "title": "实地观偏弱",
                    "detail": f"{name}有 {len(bag.filled)} 手填进自己的空。数子时填空往往不增面积，还损眼位。",
                    "moves": bag.filled,
                }
            )
        if bag.early:
            items.append(
                {
                    "player": side,
                    "title": "收官过早",
                    "detail": f"{name}有 {len(bag.early)} 次在还能占点时停着。中国规则下单官常常值得自己占上。",
                    "moves": bag.early,
                }
            )
        if bag.lost >= 3:
            items.append(
                {
                    "player": side,
                    "title": "子力被提",
                    "detail": f"{name}被提掉 {bag.lost} 子。这些子不再计入己方面积，也不会另加给对方。",
                    "moves": [],
                }
            )
        if not any(item["player"] == side for item in items):
            items.append(
                {
                    "player": side,
                    "title": "未见明显硬伤",
                    "detail": f"{name}这一侧没有成串的漏提、不接打吃或过早停着。",
                    "moves": [],
                }
            )
    return items


def _summary(state, names, end_reason, end_detail, weaknesses) -> str:
    black = names["black"]
    white = names["white"]
    head = f"黑方 {black}，白方 {white}。"
    if not state.finished:
        return (
            f"{head}对局尚未终局。以下只评已经落下的棋，"
            "死子不会被自动删除，面积也不把提子再算一遍。"
        )
    if end_reason == "two_passes" and state.score:
        score = state.score
        winner = "黑方" if state.winner == BLACK else "白方"
        amount = format_number(abs(score.win_stones))
        diff = format_number(score.area_diff_after_komi, 1)
        return (
            f"{head}双方连续停着，按中国数子终局。"
            f"黑方面积 {score.black_area}，白方面积 {score.white_area}，单官 {score.dame}。"
            f"贴 7.5 目后面积差为 {diff} 目，胜子数 = {diff} ÷ 2 = {format_number(score.win_stones)} 子。"
            f"{winner}胜 {amount} 子。提子没有再加进面积。"
        )
    reason_text = {
        "resign": "有一方认输。",
        "timeout": "竞赛外层因决策超时判负。",
        "illegal": "竞赛外层因非法落子判负。",
        "protocol": "竞赛外层因协议错误判负。",
    }.get(end_reason or "", "对局结束。")
    extra = f"{end_detail}。" if end_detail else ""
    return (
        f"{head}{reason_text}{extra}"
        "胜负不改用局面面积结算。下面的缺点只复盘已经落下的棋，死子不会被自动删除。"
    )


def _advice(weaknesses: list[dict]) -> str:
    titles = {item["title"] for item in weaknesses}
    if "治孤不及时" in titles:
        return "被打吃的大棋先处理，再去抢边角。留在棋盘上的死子在数子法里仍然算对方的。"
    if "吃子偏慢" in titles:
        return "能提净的子尽快提。提掉的子只是离开棋盘，不要在面积之外再算一遍。"
    if "收官过早" in titles:
        return "终局前先把单官和对方空里能活的点走完，再连续停着。"
    if "竞赛判负" in titles:
        return "正式协议只有 PLACE、PASS、RESIGN。每步必须在 10 秒内给出合法着法，不要发送 END_GAME。"
    return "保持气数，先应打吃，再收官。贴目是 3¾ 子，贴后面积差再除以 2 才是胜子数。"
