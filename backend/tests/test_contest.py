import time

import pytest

from app.agents import HeuristicAgent, MCTSAgent, split_command
from app.protocol import ContestLayer, ContestLoss, build_payload, parse_decision
from app.rule_engine import BLACK, WHITE, Action, RuleEngine


def test_protocol_rejects_end_game_and_bad_vertex():
    with pytest.raises(Exception) as raised:
        parse_decision({"action": "END_GAME"})
    assert "END_GAME" in str(raised.value)
    with pytest.raises(Exception):
        parse_decision({"action": "PLACE", "vertex": "I5"})


def test_payload_hides_end_game_and_sets_timeout():
    engine = RuleEngine()
    payload = build_payload(engine.new_game(), engine)
    assert payload["allowed_actions"] == ["PLACE", "PASS", "RESIGN"]
    assert "END_GAME" not in payload["allowed_actions"]
    assert payload["time_limit_ms"] == 10_000
    assert payload["rules"]["prisoners_added_to_area"] is False
    assert ContestLayer.TIMEOUT_MS == 10_000


def test_end_game_is_a_protocol_loss():
    class Bad:
        is_builtin = False

        def raw_decision(self, payload, timeout_s):
            return {"action": "END_GAME"}

    engine = RuleEngine()
    with pytest.raises(ContestLoss) as raised:
        ContestLayer().adjudicate(Bad(), engine.new_game(), engine)
    assert raised.value.reason == "protocol"
    assert raised.value.winner == WHITE


def test_illegal_place_is_a_loss():
    class Occupied:
        is_builtin = False

        def raw_decision(self, payload, timeout_s):
            return {"action": "PLACE", "x": 0, "y": 0}

    engine = RuleEngine()
    state, _info = engine.apply(engine.new_game(), Action.place(0, 0))
    with pytest.raises(ContestLoss) as raised:
        ContestLayer().adjudicate(Occupied(), state, engine)
    assert raised.value.reason == "illegal"
    assert raised.value.winner == BLACK


def test_timeout_is_adjudicated_without_waiting_out_the_agent():
    class Slow:
        is_builtin = True

        def decide(self, state, budget_ms):
            time.sleep(2)
            return Action.pass_()

    engine = RuleEngine()
    started = time.perf_counter()
    with pytest.raises(ContestLoss) as raised:
        ContestLayer().adjudicate(Slow(), engine.new_game(), engine, timeout_ms=250)
    assert raised.value.reason == "timeout"
    assert time.perf_counter() - started < 1.5


def test_heuristic_game_finishes_under_the_formula():
    engine = RuleEngine()
    black = HeuristicAgent()
    white = HeuristicAgent()
    state = engine.new_game()
    guard = 200
    while not state.finished and guard:
        agent = black if state.to_play == BLACK else white
        action = agent.decide(state, 50)
        state, _info = engine.apply(state, action)
        guard -= 1
    assert state.finished
    assert state.end_reason in ("two_passes", "resign")
    score = state.score
    assert score.prisoners_counted == 0
    assert score.win_stones == pytest.approx(score.area_diff_after_komi / 2)
    assert score.black_stones + score.white_stones + score.black_territory + score.white_territory + score.dame == 81


def test_windows_command_quotes_are_removed():
    parts = split_command(r'"C:\Python\python.exe" "D:\xiaqi\agents\stdio_random_agent.py"')
    assert parts[0].endswith("python.exe")
    assert parts[1].endswith("stdio_random_agent.py")
    assert '"' not in "".join(parts)


def test_mcts_move_is_legal():
    engine = RuleEngine()
    agent = MCTSAgent(seed=1)
    state = engine.new_game()
    started = time.perf_counter()
    action = agent.decide(state, 350)
    assert time.perf_counter() - started < 8
    engine.apply(state, action)
