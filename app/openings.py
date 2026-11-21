"""Name openings from the Lichess chess-openings data in openings.tsv.

Openings are matched by position rather than by move order, so a game that reaches a
known position through a different order still gets its name.
"""
from __future__ import annotations

import csv
import io
from functools import lru_cache
from pathlib import Path

import chess
import chess.pgn

DATA_FILE = Path(__file__).with_name("openings.tsv")


@lru_cache(maxsize=1)
def _book() -> dict[str, dict]:
    book = {}
    with DATA_FILE.open(newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            game = chess.pgn.read_game(io.StringIO(row["pgn"]))
            book[game.end().board().epd()] = {"eco": row["eco"], "name": row["name"]}
    return book


def lookup(board: chess.Board) -> dict | None:
    return _book().get(board.epd())
