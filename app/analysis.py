"""Turn engine scores into winning chances and plain-language move ratings."""
from __future__ import annotations

import math

MATE_CP = 100_000

# Drop in the mover's winning chances, in percentage points.
BLUNDER = 30.0
MISTAKE = 20.0
INACCURACY = 10.0


def white_win_percent(white_cp: int) -> float:
    """Convert a score to White's winning chances, using Lichess's published curve."""
    cp = max(-MATE_CP, min(MATE_CP, white_cp))
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)


def rate(loss: float) -> str | None:
    if loss >= BLUNDER:
        return "blunder"
    if loss >= MISTAKE:
        return "mistake"
    if loss >= INACCURACY:
        return "inaccuracy"
    return None


def rate_move(white_before: float, white_after: float, mover_is_white: bool, is_best: bool) -> dict:
    """Rate one move from the mover's point of view."""
    before = white_before if mover_is_white else 100 - white_before
    after = white_after if mover_is_white else 100 - white_after
    # Playing the engine's top move can't be a mistake. Any drop is search noise.
    loss = 0.0 if is_best else max(0.0, before - after)
    return {
        "before": round(before, 1),
        "after": round(after, 1),
        "loss": round(loss, 1),
        "rating": rate(loss),
    }
