import json

import chess
import pytest
from fastapi.testclient import TestClient

from app import chessops, coach, main
from app.storage import Store

START = chess.STARTING_FEN


def test_opening_names_follow_the_game_and_survive_leaving_the_book():
    moves = ["e2e4", "c7c5"]
    history = chessops.history(START, moves)
    assert history[0]["opening"] is None
    assert history[1]["opening"]["name"] == "King's Pawn Game"
    assert history[2]["opening"]["name"].startswith("Sicilian Defense")
    odd = chessops.history(START, [*moves, "a2a3", "a7a6", "h2h3", "h7h6", "b2b3", "b7b6"])
    assert odd[-1]["opening"] is not None


def test_opening_found_through_a_different_move_order():
    direct = chessops.history(START, ["d2d4", "d7d5", "c2c4"])[-1]["opening"]
    swapped = chessops.history(START, ["c2c4", "d7d5", "d2d4"])[-1]["opening"]
    assert direct["name"] == swapped["name"]


@pytest.mark.parametrize(
    "question, expected",
    [
        ("Should I play Nf3?", ["g1f3"]),
        ("is nf3 ok", ["g1f3"]),
        ("what about my knight to f3", ["g1f3"]),
        ("how about e4", ["e2e4"]),
        ("What should I do next?", []),
    ],
)
def test_finds_moves_in_questions(question, expected):
    moves, illegal = coach.mentioned_moves(chess.Board(), question)
    assert [move.uci() for move in moves] == expected
    assert illegal == []


def test_reports_moves_that_are_not_legal():
    moves, illegal = coach.mentioned_moves(chess.Board(), "can I play Qh5 or queen to d4")
    assert moves == []
    assert set(illegal) == {"Qh5", "queen to d4"}


def test_a_piece_on_a_square_is_not_a_pawn_move():
    moves, illegal = coach.mentioned_moves(chess.Board(), "is my pawn on e2 safe")
    assert moves == [] and illegal == []


def test_loose_pieces_are_spotted():
    board = chess.Board("4k3/8/8/3q4/4P3/8/8/4K3 w - - 0 1")
    assert coach._loose_pieces(board, chess.BLACK) == ["queen on d5 is under attack and undefended"]
    defended = chess.Board("4k3/2p5/3p4/3q4/4P3/8/8/4K3 w - - 0 1").copy()
    defended.set_piece_at(chess.E6, chess.Piece.from_symbol("p"))
    assert coach._loose_pieces(defended, chess.BLACK) == ["queen on d5 is attacked by a cheaper piece"]


def test_unverified_moves_are_flagged_but_squares_are_not():
    reply = "Your pawn on e4 is fine. Play Nf3, or try Bb5+. Avoid Qh7."
    assert coach.unverified_moves(reply, {"Nf3", "Bb5"}) == ["Qh7"]


def test_facts_go_only_in_the_newest_message():
    history = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "second"},
    ]
    messages = coach.build_messages(history, "FACTS")
    assert messages[0]["role"] == "system"
    assert messages[1]["content"] == "first"
    assert messages[-1]["content"].startswith("POSITION FACTS:\nFACTS\n\nQUESTION: second")
    assert "FACTS" not in messages[1]["content"]


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    main.store = Store(tmp_path_factory.mktemp("games"))
    with TestClient(main.app) as test_client:
        yield test_client


def _rows(response):
    return [json.loads(line) for line in response.text.strip().splitlines()]


def test_facts_describe_the_viewed_position_and_asked_move(client, monkeypatch):
    sent = {}

    def fake(messages):
        sent["messages"] = messages
        yield "Try Nf3, not Qh7."

    monkeypatch.setattr(coach, "stream_reply", fake)
    body = {
        "moves": ["e2e4", "e7e5", "d1h5", "b8c6"],
        "ply": 2,
        "you": "white",
        "messages": [{"role": "user", "content": "Is Nf3 good, or Zz9?"}],
    }
    rows = _rows(client.post("/api/chat", json=body))
    facts = sent["messages"][-1]["content"]
    assert "playing White" in facts
    assert "It is White's turn" in facts
    assert "Last move: e5 by Black" in facts
    assert "The question asks about Nf3" in facts
    assert rows[0] == {"delta": "Try Nf3, not Qh7."}
    assert rows[-1] == {"done": True, "unverified": ["Qh7"]}


def _facts_for(client, monkeypatch, moves, question="hello", fen=START):
    sent = {}

    def fake(messages):
        sent["messages"] = messages
        yield "ok"

    monkeypatch.setattr(coach, "stream_reply", fake)
    body = {"start_fen": fen, "moves": moves, "ply": len(moves), "messages": [{"role": "user", "content": question}]}
    assert client.post("/api/chat", json=body).status_code == 200
    return sent["messages"][-1]["content"]


def test_an_illegal_move_gets_no_unchecked_warning(client, monkeypatch):
    monkeypatch.setattr(coach, "stream_reply", lambda messages: iter(["Qh5 isn't legal here."]))
    body = {"moves": [], "ply": 0, "messages": [{"role": "user", "content": "Can I play Qh5?"}]}
    assert _rows(client.post("/api/chat", json=body))[-1] == {"done": True, "unverified": []}


def test_facts_say_when_it_is_the_opponents_turn(client, monkeypatch):
    sent = {}

    def fake(messages):
        sent["messages"] = messages
        yield "ok"

    monkeypatch.setattr(coach, "stream_reply", fake)
    body = {"moves": ["e2e4"], "ply": 1, "you": "white", "messages": [{"role": "user", "content": "hi"}]}
    client.post("/api/chat", json=body)
    assert "That is the opponent's turn" in sent["messages"][-1]["content"]
    body["moves"], body["ply"] = ["e2e4", "e7e5"], 2
    client.post("/api/chat", json=body)
    assert "That is the opponent's turn" not in sent["messages"][-1]["content"]


def test_a_mate_threat_is_reported(client, monkeypatch):
    facts = _facts_for(client, monkeypatch, ["e2e4", "e7e5", "f1c4", "b8c6", "d1h5"])
    assert "White's real threat, if Black ignored it: Qxf7#" in facts


def test_a_quiet_position_has_no_threat(client, monkeypatch):
    facts = _facts_for(client, monkeypatch, ["e2e4", "e7e5", "g1f3", "b8c6"])
    assert "Black has no serious threat right now" in facts
    assert "real threat" not in facts


def test_best_move_is_labelled_and_described_in_words(client, monkeypatch):
    fen = "6k1/5ppp/8/8/8/8/5PPP/3R2K1 w - - 0 1"
    facts = _facts_for(client, monkeypatch, [], fen=fen)
    assert "BEST MOVE: Rd8# (rook from d1 to d8, which is checkmate)" in facts


def test_chat_when_the_model_is_down(client, monkeypatch):
    def down(messages):
        raise coach.LlmUnavailable("refused")
        yield

    monkeypatch.setattr(coach, "stream_reply", down)
    body = {"moves": [], "ply": 0, "messages": [{"role": "user", "content": "hi"}]}
    rows = _rows(client.post("/api/chat", json=body))
    assert "isn't running" in rows[0]["error"]


def test_chat_about_a_finished_game(client, monkeypatch):
    sent = {}

    def fake(messages):
        sent["messages"] = messages
        yield "It's over."

    monkeypatch.setattr(coach, "stream_reply", fake)
    moves = ["f2f3", "e7e5", "g2g4", "d8h4"]
    body = {"moves": moves, "ply": 4, "messages": [{"role": "user", "content": "what now"}]}
    assert _rows(client.post("/api/chat", json=body))[-1]["done"] is True
    assert "a win for Black" in sent["messages"][-1]["content"]


def test_chat_rejects_bad_requests(client):
    good = {"role": "user", "content": "hi"}
    assert client.post("/api/chat", json={"moves": [], "ply": 5, "messages": [good]}).status_code == 400
    wrong_role = {"role": "system", "content": "hi"}
    assert client.post("/api/chat", json={"moves": [], "ply": 0, "messages": [wrong_role]}).status_code == 422
    bad_end = {"role": "assistant", "content": "hi"}
    assert client.post("/api/chat", json={"moves": [], "ply": 0, "messages": [bad_end]}).status_code == 400


def test_analysis_can_start_part_way_and_still_rate_the_move(client):
    moves = ["e2e4", "e7e5", "d1h5", "b8c6", "h5e5"]
    rows = _rows(client.post("/api/analyze", json={"moves": moves, "from_ply": 5}))
    assert [row["ply"] for row in rows] == [5]
    assert rows[0]["rating"] == "blunder"
    assert _rows(client.post("/api/analyze", json={"moves": moves[:1], "from_ply": 0}))[0]["ply"] == 0
    assert client.post("/api/analyze", json={"moves": moves, "from_ply": 6}).text == ""
