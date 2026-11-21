"""Bot levels.

Stockfish's own strength limit stops at about 1320, so the lower levels get weaker
by choosing among worse moves. Their names are deliberately not Elo ratings because
they haven't been calibrated.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable

import chess


@dataclass(frozen=True)
class Level:
    label: str
    random_rate: float = 0.0  # chance of any legal move at all
    window_cp: int = 0  # otherwise, any move within this much of the best
    nodes: int = 1000
    uci_elo: int | None = None  # use Stockfish's own limiter instead
    full_strength: bool = False


LEVELS: dict[str, Level] = {
    "gentle": Level("Gentle", random_rate=0.35, window_cp=300, nodes=2_000),
    "easy": Level("Easy", random_rate=0.20, window_cp=200, nodes=5_000),
    "medium": Level("Medium", random_rate=0.10, window_cp=100, nodes=20_000),
    "club": Level("Club (about 1320)", uci_elo=1320, nodes=100_000),
    "full": Level("Full Stockfish", full_strength=True, nodes=1_000_000),
}


# Each candidate is (move, score for the side to move in centipawns), best first.
Candidates = list[tuple[chess.Move, int]]


def choose(candidates: Candidates, level: Level, rng: random.Random) -> chess.Move:
    """Pick a weakened move from candidates ranked by the engine."""
    if rng.random() < level.random_rate:
        return rng.choice(candidates)[0]
    best = candidates[0][1]
    allowed = [move for move, score in candidates if best - score <= level.window_cp]
    return rng.choice(allowed)


def pick_move(
    board: chess.Board,
    level: Level,
    get_candidates: Callable[[chess.Board, int], Candidates],
    rng: random.Random,
) -> chess.Move:
    return choose(get_candidates(board, level.nodes), level, rng)
