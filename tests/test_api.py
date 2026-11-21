"""End-to-end API tests against a real Stockfish process."""
import json

import pytest
from fastapi.testclient import TestClient

from app import main
from app.storage import Store


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    main.store = Store(tmp_path_factory.mktemp("games"))
    with TestClient(main.app) as test_client:
        yield test_client


def test_every_bot_level_plays_a_legal_move(client):
    for level in client.get("/api/levels").json():
        response = client.post("/api/bot-move", json={"moves": ["e2e4"], "level": level["id"]})
        assert response.status_code == 200, level
        assert response.json()["turn"] == "white"


def test_illegal_move_returns_400(client):
    assert client.post("/api/move", json={"moves": [], "uci": "e2e5"}).status_code == 400


def test_full_strength_takes_a_free_queen(client):
    fen = "4k3/8/8/3q4/4P3/8/8/4K3 w - - 0 1"
    response = client.post("/api/bot-move", json={"start_fen": fen, "level": "full"})
    assert response.json()["uci"] == "e4d5"


def test_analysis_flags_a_hung_queen(client):
    # 1. e4 e5 2. Qh5 Nc6 3. Qxe5+?? gives away the queen to Nxe5.
    moves = ["e2e4", "e7e5", "d1h5", "b8c6", "h5e5"]
    response = client.post("/api/analyze", json={"moves": moves})
    rows = [json.loads(line) for line in response.text.strip().splitlines()]
    assert [row["ply"] for row in rows] == list(range(6))
    assert rows[5]["san"] == "Qxe5+"
    assert rows[5]["rating"] == "blunder"
    assert rows[5]["best_san"] != "Qxe5+"


def test_analysis_handles_checkmate(client):
    moves = ["f2f3", "e7e5", "g2g4", "d8h4"]
    response = client.post("/api/analyze", json={"moves": moves})
    rows = [json.loads(line) for line in response.text.strip().splitlines()]
    assert rows[-1]["white"] == 0.0


def test_analysis_gives_the_best_move_for_every_position(client):
    # A position with no moves played: White can take a free queen.
    fen = "4k3/8/8/3q4/4P3/8/8/4K3 w - - 0 1"
    response = client.post("/api/analyze", json={"start_fen": fen, "moves": []})
    rows = [json.loads(line) for line in response.text.strip().splitlines()]
    assert len(rows) == 1
    assert rows[0]["next_best"] == "e4d5"

    # The game is over after checkmate, so there's no next move.
    moves = ["f2f3", "e7e5", "g2g4", "d8h4"]
    response = client.post("/api/analyze", json={"moves": moves})
    rows = [json.loads(line) for line in response.text.strip().splitlines()]
    assert rows[2]["next_best"] is not None
    assert rows[-1]["next_best"] is None


def test_saved_game_round_trip(client):
    game = {"created_at": "2026-11-06T00:00:00Z", "moves": ["e2e4"], "human": [True]}
    assert client.put("/api/games/g1", json=game).status_code == 200
    assert client.get("/api/games/g1").json()["moves"] == ["e2e4"]
    assert client.get("/api/games/missing").status_code == 404
    bad = {**game, "moves": ["e2e5"]}
    assert client.put("/api/games/g2", json=bad).status_code == 400


def test_deleting_a_saved_game(client):
    game = {"created_at": "2026-11-06T00:00:00Z", "moves": ["e2e4"], "human": [True]}
    client.put("/api/games/keep", json=game)
    client.put("/api/games/gone", json=game)
    assert client.delete("/api/games/gone").json() == {"deleted": "gone"}
    assert client.get("/api/games/gone").status_code == 404
    assert client.delete("/api/games/gone").status_code == 404
    ids = [saved["id"] for saved in client.get("/api/games").json()]
    assert "keep" in ids and "gone" not in ids
    assert client.delete("/api/games/bad.id").status_code == 400
    assert client.delete("/api/games/..%2Fsecret").status_code >= 400


def test_browser_must_check_for_newer_app_files(client):
    first = client.get("/app.js")
    assert first.headers["cache-control"] == "no-cache"
    again = client.get("/app.js", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304
    assert again.headers["cache-control"] == "no-cache"
