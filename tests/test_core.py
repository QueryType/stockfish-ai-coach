import random

import chess
import pytest

from app import chessops
from app.analysis import rate_move, white_win_percent
from app.bot import Level, choose
from app.storage import Store

START = chess.STARTING_FEN
PROMOTION_FEN = "8/P6k/8/8/8/8/8/K7 w - - 0 1"


def test_rejects_illegal_move():
    with pytest.raises(ValueError):
        chessops.replay(START, ["e2e5"])


def test_rejects_bad_fen():
    with pytest.raises(ValueError):
        chessops.board_from_fen("not a position")


def test_legal_moves_and_sans():
    info = chessops.position_info(START, ["e2e4"])
    assert info["turn"] == "black"
    assert info["sans"] == ["e4"]
    assert "e5" in info["dests"]["e7"]


def test_promotion_defaults_to_queen_and_lists_one_target():
    assert chessops.normalize_move(PROMOTION_FEN, [], "a7a8") == "a7a8q"
    assert chessops.position_info(PROMOTION_FEN, [])["dests"]["a7"] == ["a8"]


def test_checkmate_ends_game_with_no_moves():
    info = chessops.position_info(START, ["f2f3", "e7e5", "g2g4", "d8h4"])
    assert info["status"] == {"over": True, "result": "0-1", "reason": "Checkmate"}
    assert info["dests"] == {}


def test_parse_pgn():
    game = chessops.parse_pgn('[White "Ann"]\n\n1. e4 e5 2. Nf3 *')
    assert game["moves"] == ["e2e4", "e7e5", "g1f3"]
    assert game["white"] == "Ann"


@pytest.mark.parametrize("text", ["", "hello there", "1. e4 e5 2. Qh8 *"])
def test_parse_pgn_rejects_junk(text):
    with pytest.raises(ValueError):
        chessops.parse_pgn(text)


def test_win_percent_shape():
    assert white_win_percent(0) == pytest.approx(50)
    assert white_win_percent(100_000) == pytest.approx(100)
    assert white_win_percent(-100_000) == pytest.approx(0)
    assert white_win_percent(300) > 70


def test_rating_uses_the_movers_point_of_view():
    # White's chances rise from 30 to 80, so Black's fall from 70 to 20.
    assert rate_move(30, 80, mover_is_white=False, is_best=False)["rating"] == "blunder"
    assert rate_move(30, 80, mover_is_white=True, is_best=False)["rating"] is None


def test_best_move_is_never_rated_a_mistake():
    assert rate_move(80, 30, mover_is_white=True, is_best=True)["rating"] is None


def test_bot_stays_inside_its_window():
    a, b, c = (chess.Move.from_uci(u) for u in ("e2e4", "d2d4", "a2a3"))
    level = Level("t", random_rate=0, window_cp=50)
    picks = {choose([(a, 100), (b, 60), (c, -200)], level, random.Random(i)) for i in range(50)}
    assert picks == {a, b}


def test_bot_random_rate_can_pick_anything():
    a, c = (chess.Move.from_uci(u) for u in ("e2e4", "a2a3"))
    level = Level("t", random_rate=1.0, window_cp=0)
    picks = {choose([(a, 100), (c, -900)], level, random.Random(i)) for i in range(50)}
    assert c in picks


def test_store_round_trip_and_blocks_path_traversal(tmp_path):
    store = Store(tmp_path)
    store.save("abc-1", {"moves": ["e2e4"]})
    assert store.load("abc-1")["moves"] == ["e2e4"]
    assert store.list()[0]["move_count"] == 1
    (tmp_path / "broken.json").write_text("{")
    assert len(store.list()) == 1
    with pytest.raises(ValueError):
        store.load("../../etc/passwd")
