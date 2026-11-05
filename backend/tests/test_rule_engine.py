import pytest

from app.rule_engine import (
    BLACK,
    WHITE,
    Action,
    IllegalMove,
    RuleEngine,
)


def _ko_ready():
    board = bytearray(81)
    stones = {
        (2, 2): WHITE,
        (2, 3): BLACK,
        (2, 1): BLACK,
        (1, 2): BLACK,
        (4, 2): WHITE,
        (3, 3): WHITE,
        (3, 1): WHITE,
    }
    for (x, y), color in stones.items():
        board[y * 9 + x] = color
    return bytes(board)


def test_black_plays_first():
    assert RuleEngine().new_game().to_play == BLACK


def test_suicide_is_illegal():
    board = bytearray(81)
    board[1] = WHITE
    board[9] = WHITE
    engine = RuleEngine()
    state = engine.from_position(bytes(board), BLACK)
    with pytest.raises(IllegalMove) as raised:
        engine.apply(state, Action.place(0, 0))
    assert raised.value.code == "suicide"


def test_capture_is_not_suicide_and_prisoners_are_not_added():
    board = bytearray(81)
    board[0] = WHITE
    board[1] = BLACK
    engine = RuleEngine()
    state = engine.from_position(bytes(board), BLACK)
    new_state, info = engine.apply(state, Action.place(0, 1))
    assert info.captured == ((0, 0),)
    score = engine.score_board(new_state.board)
    assert score.white_stones == 0
    assert score.black_area == score.black_stones + score.black_territory
    assert score.black_area == 81
    assert score.prisoners_counted == 0
    assert score.black_stones + score.white_stones + score.black_territory + score.white_territory + score.dame == 81


def test_double_capture():
    board = bytearray(81)
    board[19] = WHITE  # (1, 2)
    board[1] = WHITE  # (1, 0)
    board[1 + 3 * 9] = BLACK
    board[2 * 9] = BLACK
    board[2 + 2 * 9] = BLACK
    board[0] = BLACK
    board[2] = BLACK
    engine = RuleEngine()
    _state, info = engine.apply(engine.from_position(bytes(board), BLACK), Action.place(1, 1))
    assert set(info.captured) == {(1, 2), (1, 0)}


def test_immediate_recapture_hits_global_superko():
    engine = RuleEngine()
    before = engine.from_position(_ko_ready(), BLACK)
    after, info = engine.apply(before, Action.place(3, 2))
    assert (2, 2) in info.captured
    with pytest.raises(IllegalMove) as raised:
        engine.apply(after, Action.place(2, 2))
    assert raised.value.code == "superko"
    restored, _info = engine.apply(engine.from_position(after.board, WHITE), Action.place(2, 2))
    assert restored.board == before.board


def test_superko_rejects_any_earlier_shape():
    engine = RuleEngine()
    before = engine.from_position(_ko_ready(), BLACK)
    after, _info = engine.apply(before, Action.place(3, 2))
    blocked = engine.from_position(after.board, WHITE, previous_boards=[before.board])
    with pytest.raises(IllegalMove) as raised:
        engine.apply(blocked, Action.place(2, 2))
    assert raised.value.code == "superko"


def test_two_passes_score_with_komi_formula():
    engine = RuleEngine()
    state, _info = engine.apply(engine.new_game(), Action.pass_())
    assert state.finished is False
    assert state.consecutive_passes == 1
    finished, _info = engine.apply(state, Action.pass_())
    assert finished.finished is True
    assert finished.end_reason == "two_passes"
    assert finished.winner == WHITE
    score = finished.score
    assert score.area_diff_after_komi == pytest.approx(-7.5)
    assert score.win_stones == pytest.approx(score.area_diff_after_komi / 2)
    assert score.win_stones == pytest.approx(-3.75)
    assert score.prisoners_counted == 0


def test_a_stone_cancels_the_pass_streak():
    engine = RuleEngine()
    passed, _info = engine.apply(engine.new_game(), Action.pass_())
    played, _info = engine.apply(passed, Action.place(4, 4))
    assert played.consecutive_passes == 0
    assert played.finished is False
    assert played.to_play == BLACK


def test_area_formula_on_a_filled_board():
    board = bytearray(81)
    for index in range(45):
        board[index] = BLACK
    for index in range(45, 81):
        board[index] = WHITE
    score = RuleEngine().score_board(bytes(board))
    assert score.black_area == 45
    assert score.white_area == 36
    assert score.dame == 0
    assert score.area_diff_after_komi == pytest.approx(1.5)
    assert score.win_stones == pytest.approx(0.75)
    assert score.winner == BLACK
    assert score.prisoners_counted == 0


def test_resign_ignores_the_board_score():
    board = bytearray(81)
    for index in range(60):
        board[index] = BLACK
    for index in range(60, 81):
        board[index] = WHITE
    engine = RuleEngine()
    state = engine.from_position(bytes(board), BLACK)
    assert engine.score_board(state.board).winner == BLACK
    resigned, _info = engine.apply(state, Action.resign())
    assert resigned.winner == WHITE
    assert resigned.end_reason == "resign"
    assert resigned.score.winner == BLACK


def test_end_game_is_not_a_legal_engine_action():
    with pytest.raises(IllegalMove) as raised:
        RuleEngine().apply(RuleEngine().new_game(), Action("END_GAME"))
    assert raised.value.code == "protocol"


def test_occupied_point_is_rejected():
    engine = RuleEngine()
    state, _info = engine.apply(engine.new_game(), Action.place(0, 0))
    with pytest.raises(IllegalMove) as raised:
        engine.apply(state, Action.place(0, 0))
    assert raised.value.code == "occupied"
