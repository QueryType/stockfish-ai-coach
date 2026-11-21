"""Chess rules: replay moves, list legal moves and read PGN files.

python-chess is the only authority on the rules. The browser never decides
whether a move is legal.
"""
from __future__ import annotations

import io

import chess
import chess.pgn

from .openings import lookup as lookup_opening


def board_from_fen(fen: str) -> chess.Board:
    try:
        board = chess.Board(fen)
    except ValueError as exc:
        raise ValueError(f"That position isn't valid: {exc}") from exc
    if not board.is_valid():
        raise ValueError("That position isn't legal, for example a side has no king.")
    return board


def replay(start_fen: str, moves: list[str]) -> chess.Board:
    board = board_from_fen(start_fen)
    for uci in moves:
        move = _parse_legal(board, uci)
        board.push(move)
    return board


def _parse_legal(board: chess.Board, uci: str) -> chess.Move:
    try:
        move = chess.Move.from_uci(uci)
    except ValueError as exc:
        raise ValueError(f"Not a valid move: {uci}") from exc
    if move not in board.legal_moves:
        raise ValueError(f"Illegal move: {uci}")
    return move


def normalize_move(start_fen: str, moves: list[str], uci: str) -> str:
    """Return the legal form of a move. A pawn reaching the last rank becomes a queen."""
    board = replay(start_fen, moves)
    if len(uci) == 4:
        queen = uci + "q"
        if chess.Move.from_uci(queen) in board.legal_moves:
            return queen
    return _parse_legal(board, uci).uci()


def status(board: chess.Board) -> dict:
    outcome = board.outcome(claim_draw=True)
    if outcome is None:
        return {"over": False, "result": "*", "reason": None}
    reason = outcome.termination.name.replace("_", " ").capitalize()
    return {"over": True, "result": outcome.result(), "reason": reason}


def position_info(start_fen: str, moves: list[str]) -> dict:
    board = replay(start_fen, moves)
    dests: dict[str, list[str]] = {}
    for move in board.legal_moves:
        targets = dests.setdefault(chess.square_name(move.from_square), [])
        target = chess.square_name(move.to_square)
        if target not in targets:  # the four promotion moves share one target
            targets.append(target)
    state = status(board)
    if state["over"]:
        dests = {}
    return {
        "fen": board.fen(),
        "turn": "white" if board.turn == chess.WHITE else "black",
        "check": board.is_check(),
        "dests": dests,
        "sans": sans(start_fen, moves),
        "history": history(start_fen, moves),
        "status": state,
    }


def sans(start_fen: str, moves: list[str]) -> list[str]:
    board = board_from_fen(start_fen)
    out = []
    for uci in moves:
        move = _parse_legal(board, uci)
        out.append(board.san(move))
        board.push(move)
    return out


def history(start_fen: str, moves: list[str]) -> list[dict]:
    """Return every position in the line, so the browser can step through them."""
    board = board_from_fen(start_fen)
    opening = None

    def snapshot() -> dict:
        nonlocal opening
        opening = lookup_opening(board) or opening  # keep the name after leaving the book
        turn = "white" if board.turn == chess.WHITE else "black"
        return {"fen": board.fen(), "turn": turn, "check": board.is_check(), "opening": opening}

    out = [snapshot()]
    for uci in moves:
        board.push(_parse_legal(board, uci))
        out.append(snapshot())
    return out


def parse_pgn(text: str) -> dict:
    game = chess.pgn.read_game(io.StringIO(text))
    if game is None:
        raise ValueError("I couldn't find a game in that text.")
    if game.errors:
        raise ValueError(f"That game has a problem: {game.errors[0]}")
    moves = [move.uci() for move in game.mainline_moves()]
    if not moves:
        raise ValueError("I couldn't find any moves in that text.")
    return {
        "start_fen": game.board().fen(),
        "moves": moves,
        "white": game.headers.get("White", "White"),
        "black": game.headers.get("Black", "Black"),
    }
