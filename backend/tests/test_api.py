import sys

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("XIAQI_DB", str(tmp_path / "xiaqi.db"))
    from app.main import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


def test_rules_are_published(client):
    rules = client.get("/api/rules").json()
    assert rules["board_size"] == 9
    assert rules["first_player"] == "black"
    assert rules["komi_stones"] == 3.75
    assert rules["komi_points"] == 7.5
    assert rules["prisoners_added_to_area"] is False
    assert rules["dead_stone_removal"] == "must_capture"
    assert rules["superko"] == "global_positional"
    assert rules["ai_actions"] == ["PLACE", "PASS", "RESIGN"]
    assert "END_GAME" in rules["ai_actions_forbidden"]
    assert rules["decision_timeout_ms"] == 10000


def test_two_passes_store_score_analysis_and_sgf(client):
    created = client.post("/api/games", json={"black_id": "human", "white_id": "human"}).json()
    game_id = created["id"]
    assert created["to_play"] == "black"
    first = client.post(f"/api/games/{game_id}/move", json={"action": "PASS"})
    assert first.status_code == 200
    done = client.post(f"/api/games/{game_id}/move", json={"action": "PASS"}).json()
    assert done["status"] == "finished"
    assert done["result"]["winner"] == "white"
    assert done["result"]["end_reason"] == "two_passes"
    assert done["result"]["forfeit"] is False
    assert done["result"]["win_stones"] == pytest.approx(-3.75)
    assert done["result"]["score"]["area_diff_after_komi"] == pytest.approx(-7.5)
    assert done["result"]["score"]["prisoners_counted"] == 0
    assert "胜子数" in done["analysis"]["summary"]
    assert done["analysis"]["weaknesses"]
    sgf = client.get(f"/api/games/{game_id}/sgf").text
    assert "SZ[9]" in sgf
    assert "KM[7.5]" in sgf
    assert "RU[Chinese]" in sgf
    assert "胜子数" in sgf


def test_human_illegal_click_does_not_end_the_game(client):
    created = client.post("/api/games", json={"black_id": "human", "white_id": "human"}).json()
    game_id = created["id"]
    assert client.post(f"/api/games/{game_id}/move", json={"action": "PLACE", "x": 0, "y": 0}).status_code == 200
    rejected = client.post(f"/api/games/{game_id}/move", json={"action": "PLACE", "x": 0, "y": 0})
    assert rejected.status_code == 400
    current = client.get(f"/api/games/{game_id}").json()
    assert current["status"] == "playing"
    assert current["move_count"] == 1


def test_agent_end_game_and_illegal_move_are_losses(client, tmp_path):
    end_game = tmp_path / "end_game.py"
    end_game.write_text(
        "import json,sys\nsys.stdin.readline()\nprint(json.dumps({'action':'END_GAME'}))\n",
        encoding="utf-8",
    )
    illegal = tmp_path / "illegal.py"
    illegal.write_text(
        "import json,sys\nsys.stdin.readline()\nprint(json.dumps({'action':'PLACE','vertex':'A1'}))\n",
        encoding="utf-8",
    )
    python = sys.executable
    end_agent = client.post(
        "/api/agents",
        json={"name": "抢分", "protocol": "stdio", "command": f'"{python}" "{end_game}"'},
    ).json()
    bad_agent = client.post(
        "/api/agents",
        json={"name": "占子", "protocol": "stdio", "command": f'"{python}" "{illegal}"'},
    ).json()

    opened = client.post(
        "/api/games",
        json={"black_id": end_agent["id"], "white_id": "human"},
    ).json()
    lost = client.post(f"/api/games/{opened['id']}/step", json={"think_ms": 1000}).json()
    assert lost["result"]["winner"] == "white"
    assert lost["result"]["end_reason"] == "protocol"
    assert lost["result"]["win_stones"] is None
    assert "END_GAME" in lost["result"]["detail"]

    fight = client.post(
        "/api/games",
        json={"black_id": "human", "white_id": bad_agent["id"]},
    ).json()
    assert client.post(
        f"/api/games/{fight['id']}/move",
        json={"action": "PLACE", "vertex": "A1"},
    ).status_code == 200
    judged = client.post(f"/api/games/{fight['id']}/step", json={"think_ms": 1000}).json()
    assert judged["result"]["winner"] == "black"
    assert judged["result"]["end_reason"] == "illegal"
    assert judged["analysis"]["summary"]
