import json
import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import demo_ai
from app.main import create_app
from app.protocol import build_payload
from app.rule_engine import Action, RuleEngine


@pytest.fixture
def demo_client(tmp_path, monkeypatch):
    monkeypatch.setenv("XIAQI_DB", str(tmp_path / "demo.db"))
    with TestClient(create_app()) as client:
        yield client


def test_demo_evidence_replays_and_downloads(demo_client):
    overview = demo_client.get("/api/demo").json()
    assert overview["training_games"] == 256
    assert overview["new_positions"] == 21300
    assert len(overview["replays"]) == 200
    assert sum(r["result"] > 0 for r in overview["replays"]) == overview["primary"]["wins"] == 139
    replay = demo_client.get("/api/demo/replays/0").json()
    assert replay["readonly"] and replay["status"] == "finished"
    assert len(replay["frames"]) == len(replay["moves"]) + 1
    assert not any(any(row) for row in replay["frames"][0]["board"])
    assert replay["result"]["end_reason"] == "two_passes"
    assert demo_client.get("/api/demo/replays/-1").status_code == 404
    assert demo_client.get("/api/demo/replays/200").status_code == 404
    sgf = demo_client.get("/api/demo/replays/0/sgf")
    assert sgf.status_code == 200 and "KM[7.5]" in sgf.text and "SZ[9]" in sgf.text
    assert "attachment" in sgf.headers["content-disposition"]
    assert "139 胜" in demo_client.get("/api/demo/report").text
    assert "让 AI" in demo_client.get("/").text
    assert "电子斗蛐蛐" in demo_client.get("/arena").text


def test_native_adapter_preserves_axes_history_and_rules(monkeypatch):
    engine = RuleEngine()
    initial = engine.new_game()
    state, _ = engine.apply(initial, Action.place(0, 0))
    payload = build_payload(state, engine, [{"action": "PLACE", "x": 0, "y": 0}])
    monkeypatch.setattr(demo_ai, "model_status", lambda _: {"available": True})
    captured = {}

    def run(command, **kwargs):
        requests = [json.loads(line) for line in kwargs["input"].splitlines()]
        captured.update(kwargs)
        assert len(requests) == 2
        warmup, actual = requests
        assert warmup["observation"]["pass_forbidden_after_dispute"]
        assert warmup["observation"]["legal_moves"] == []
        obs = actual["observation"]
        assert obs["board"][8][0] == 1 and obs["board"][0][0] == 0
        assert obs["last_action"] == {"kind": "PLACE", "row": 8, "col": 0}
        assert obs["current_player"] == 2 and obs["passes_to_score"] == 2
        assert {"row": 8, "col": 0} not in obs["legal_moves"]
        assert kwargs["env"]["GO9_SIMS"] == "32"
        return SimpleNamespace(returncode=0, stdout="{}\n" + json.dumps({
            "protocol": "arena.v1", "type": "move_response", "request_id": actual["request_id"],
            "action": {"kind": "PLACE", "row": 1, "col": 3},
        }) + "\n")

    monkeypatch.setattr(demo_ai.subprocess, "run", run)
    decision = demo_ai.DemoAgent("demo:trained").raw_decision(payload, 10)
    assert decision == {"action": "PLACE", "x": 3, "y": 7}
    assert captured["cwd"].name == "trained"


@pytest.mark.skipif(os.name != "nt", reason="Frozen native demo is for Windows")
@pytest.mark.parametrize("model", ["demo:trained", "demo:baseline"])
def test_actual_native_model_play_and_reload(demo_client, model):
    assert demo_ai.model_status(model)["available"]
    game = demo_client.post("/api/games", json={"black_id": "human", "white_id": model}).json()
    path = f"/api/games/{game['id']}"
    assert demo_client.post(path + "/move", json={"action": "PLACE", "x": 0, "y": 0}).status_code == 200
    legal = demo_client.get(path).json()["legal_moves"]
    replied = demo_client.post(path + "/step", json={"think_ms": 2500}).json()
    assert replied["status"] == "playing"
    assert replied["move_count"] == 2
    last = replied["moves"][-1]
    assert last["action"] == "PLACE"
    assert any(m["x"] == last["x"] and m["y"] == last["y"] for m in legal)
    # Each decision starts a new process and restores all real positions, including
    # the asymmetric first move, so reloading does not lose board orientation.
    assert demo_client.post(path + "/move", json={"action": "PASS"}).status_code == 200
    again = demo_client.post(path + "/step", json={"think_ms": 2500}).json()
    assert again["move_count"] == 4
    assert again["moves"][-1]["action"] in ("PLACE", "PASS")
    assert not again["result"] or again["result"]["end_reason"] == "two_passes"


def test_missing_model_refuses_game_instead_of_fallback(demo_client, monkeypatch, tmp_path):
    monkeypatch.setattr(demo_ai, "MODEL_ROOT", tmp_path / "missing-models")
    response = demo_client.post("/api/games", json={"black_id": "human", "white_id": "demo:trained"})
    assert response.status_code == 503
    assert "缺失" in response.json()["detail"]
