"""A thread-safe wrapper around one Stockfish process."""
from __future__ import annotations

import random
import shutil
import threading

import chess
import chess.engine

from .analysis import MATE_CP, white_win_percent
from .bot import Candidates, Level, pick_move

ANALYSIS_NODES = 300_000


class Engine:
    def __init__(self, threads: int = 4, hash_mb: int = 256) -> None:
        path = shutil.which("stockfish")
        if path is None:
            raise RuntimeError("Stockfish isn't installed. Run: brew install stockfish")
        self._engine = chess.engine.SimpleEngine.popen_uci(path)
        self._engine.configure({"Threads": threads, "Hash": hash_mb})
        self._lock = threading.Lock()
        self._rng = random.Random()

    def close(self) -> None:
        with self._lock:
            self._engine.quit()

    def _candidates(self, board: chess.Board, nodes: int) -> Candidates:
        count = board.legal_moves.count()
        infos = self._engine.analyse(board, chess.engine.Limit(nodes=nodes), multipv=count)
        return [
            (info["pv"][0], info["score"].pov(board.turn).score(mate_score=MATE_CP))
            for info in infos
            if info.get("pv")
        ]

    def bot_move(self, board: chess.Board, level: Level) -> chess.Move:
        with self._lock:
            limit = chess.engine.Limit(nodes=level.nodes)
            if level.full_strength:
                return self._engine.play(board, limit).move
            if level.uci_elo is not None:
                options = {"UCI_LimitStrength": True, "UCI_Elo": level.uci_elo}
                return self._engine.play(board, limit, options=options).move
            return pick_move(board, level, self._candidates, self._rng)

    def top_lines(self, board: chess.Board, count: int = 3, plies: int = 5) -> list[dict]:
        """Return the engine's best few lines, each with White's winning chances."""
        if board.outcome(claim_draw=True) is not None:
            return []
        with self._lock:
            infos = self._engine.analyse(
                board, chess.engine.Limit(nodes=ANALYSIS_NODES), multipv=count
            )
        lines = []
        for info in infos:
            pv = info.get("pv") or []
            if not pv:
                continue
            walker = board.copy()
            sans = []
            for move in pv[:plies]:
                sans.append(walker.san(move))
                walker.push(move)
            score = info["score"].white().score(mate_score=MATE_CP)
            lines.append({"sans": sans, "white": round(white_win_percent(score), 1)})
        return lines

    def evaluate(self, board: chess.Board) -> dict:
        """Return White's winning chances and the best move for this position."""
        outcome = board.outcome(claim_draw=True)
        if outcome is not None:
            if outcome.winner is None:
                white = 50.0
            else:
                white = 100.0 if outcome.winner == chess.WHITE else 0.0
            return {"white": white, "best": None, "best_san": None}
        with self._lock:
            info = self._engine.analyse(board, chess.engine.Limit(nodes=ANALYSIS_NODES))
        score = info["score"].white().score(mate_score=MATE_CP)
        pv = info.get("pv") or []
        best = pv[0] if pv else None
        return {
            "white": round(white_win_percent(score), 1),
            "best": best.uci() if best else None,
            "best_san": board.san(best) if best else None,
        }
