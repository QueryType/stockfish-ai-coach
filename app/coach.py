"""Chat: Stockfish and python-chess work out the facts, and the local LLM explains them.

The LLM never analyses the position itself. Every question is sent with a block of
verified facts about the board, including any moves the question mentions. Moves in the
reply that those facts don't support are reported back so the app can warn about them.
"""
from __future__ import annotations

import json
import os
import re
from typing import Iterator

import chess
import httpx

from .analysis import rate_move

LLM_URL = os.environ.get("LLM_URL", "http://127.0.0.1:8080/v1").rstrip("/")
LLM_START_COMMAND = os.environ.get(
    "LLM_START_COMMAND", "llama-server --model /path/to/model.gguf --host 127.0.0.1 --port 8080"
)
MAX_MENTIONED_MOVES = 3
THREAT_DROP = 15  # points of winning chances

SYSTEM_PROMPT = """You are a warm, patient chess coach talking to a beginner rated about 750.

Each question arrives with POSITION FACTS from Stockfish and a rules checker. They are \
correct and describe the board as it is now. Earlier messages may be about other positions.

Rules for questions about the position:
- Base every claim about this position on the facts. You can't see the board any other way, \
so don't work out moves or variations yourself.
- When asked what to play, recommend the move labelled BEST MOVE. Never recommend a different \
move, even if another seems natural.
- Only name moves that appear in the facts. Don't warn against moves that aren't in the \
facts, and don't predict replies the facts don't give. If the facts don't answer the \
question, say you aren't sure.
- Don't explain why other moves are worse unless the facts say so, and don't describe a \
piece as trapped, weak or strong unless the facts say so.
- Only describe something as a threat if the facts call it a real threat.
- Don't reveal the best move unless the question asks what to play.
- If the facts say a move is illegal, say so plainly and don't guess why.
- Give the reason a move is good or bad using the facts: what it attacks, defends or leaves \
undefended, and what the opponent can do next.

Other chess questions, like general ideas, books or how a piece moves, you may answer from \
general knowledge, without claiming anything about this position. For anything unrelated to \
chess, say kindly that you can only help with chess.

Style: use everyday words and name pieces with their squares, like "your knight on f3". \
Explain any chess term you use. Treat winning chances as rough estimates. Answer what was \
asked in under 120 words unless asked for more. Write plain sentences with no markdown, \
headings or tables."""

_PIECE_WORDS = {
    "knight": chess.KNIGHT,
    "bishop": chess.BISHOP,
    "rook": chess.ROOK,
    "queen": chess.QUEEN,
    "king": chess.KING,
    "pawn": chess.PAWN,
}
_PIECE_NAMES = {value: key for key, value in _PIECE_WORDS.items()}
_VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}

_PIECE_TO_SQUARE = re.compile(
    r"\b(knight|bishop|rook|queen|king|pawn)\s+(?:to|takes|captures)\s+([a-h][1-8])\b", re.I
)
_PIECE_ON_SQUARE = re.compile(r"\b(?:on|at|from|of)\s+[a-h][1-8]\b", re.I)
_SAN_IN_QUESTION = re.compile(
    r"(?<![\w-])(O-O-O|O-O|[KQRBNkqrbn]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?)(?![\w-])"
)
_MOVE_IN_REPLY = re.compile(
    r"(?<![\w-])([KQRBN][a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?|[a-h]x[a-h][1-8](?:=[QRBN])?[+#]?)(?![\w-])"
)
# A bare "e4" is usually a square, so pawn moves only count after a word like "play".
_PAWN_MOVE_IN_REPLY = re.compile(
    r"\b(?:play|plays|playing|try|push|pushing|advance)\s+([a-h][1-8](?:=[QRBN])?[+#]?)(?![\w-])", re.I
)


class LlmUnavailable(Exception):
    pass


def _side(color: chess.Color) -> str:
    return "White" if color == chess.WHITE else "Black"


def _chances(white: float, color: chess.Color) -> int:
    return round(white if color == chess.WHITE else 100 - white)


def _strip(san: str) -> str:
    return san.rstrip("+#")


def mentioned_moves(board: chess.Board, question: str) -> tuple[list[chess.Move], list[str]]:
    """Find moves in a question, returning the legal ones and the text of any others."""
    moves: list[chess.Move] = []
    illegal: list[str] = []

    def add(move: chess.Move) -> None:
        if move not in moves:
            moves.append(move)

    for match in _PIECE_TO_SQUARE.finditer(question):
        piece_type = _PIECE_WORDS[match.group(1).lower()]
        target = chess.parse_square(match.group(2))
        options = [
            move
            for move in board.legal_moves
            if move.to_square == target and board.piece_type_at(move.from_square) == piece_type
        ]
        if options:
            add(options[0])
        else:
            illegal.append(match.group(0))

    # "my knight on f3" names a piece, not a pawn move to f3.
    text = _PIECE_TO_SQUARE.sub(" ", question)
    text = _PIECE_ON_SQUARE.sub(" ", text)
    for token in _SAN_IN_QUESTION.findall(text):
        move = _parse_san(board, token)
        if move is None:
            illegal.append(token)
        else:
            add(move)
    return moves[:MAX_MENTIONED_MOVES], illegal[:MAX_MENTIONED_MOVES]


def _parse_san(board: chess.Board, token: str) -> chess.Move | None:
    attempts = [token]
    if token[0] in "kqrbn" and len(token) >= 3:
        attempts.append(token[0].upper() + token[1:])  # people often type "nf3"
    for attempt in attempts:
        try:
            return board.parse_san(attempt)
        except ValueError:
            continue
    return None


def _piece_list(board: chess.Board, color: chess.Color) -> list[str]:
    pieces = []
    for piece_type, letter in (
        (chess.KING, "K"),
        (chess.QUEEN, "Q"),
        (chess.ROOK, "R"),
        (chess.BISHOP, "B"),
        (chess.KNIGHT, "N"),
        (chess.PAWN, ""),
    ):
        for square in board.pieces(piece_type, color):
            pieces.append(letter + chess.square_name(square))
    return pieces


def _material(board: chess.Board, color: chess.Color) -> int:
    return sum(len(board.pieces(t, color)) * v for t, v in _VALUES.items())


def _loose_pieces(board: chess.Board, color: chess.Color) -> list[str]:
    """List this side's pieces that are undefended or attacked by something cheaper."""
    notes = []
    for square, piece in board.piece_map().items():
        if piece.color != color or piece.piece_type == chess.KING:
            continue
        attackers = board.attackers(not color, square)
        if not attackers:
            continue
        name = f"{_PIECE_NAMES[piece.piece_type]} on {chess.square_name(square)}"
        cheapest = min(_VALUES[board.piece_type_at(a)] for a in attackers)
        if not board.attackers(color, square):
            notes.append(f"{name} is under attack and undefended")
        elif cheapest < _VALUES[piece.piece_type]:
            notes.append(f"{name} is attacked by a cheaper piece")
    return notes


def _passed_turn(board: chess.Board) -> chess.Board | None:
    """Return the same position with the other side to move, if that's a legal position."""
    if board.is_check():
        return None
    passed = board.copy(stack=False)
    passed.turn = not board.turn
    passed.ep_square = None
    return passed if passed.is_valid() else None


def _threat(engine, board: chess.Board, white_now: float) -> tuple[str, str] | None:
    """Find a move the opponent could make next that would really hurt, with its description.

    The opponent always has a best move, but most aren't threats. It only counts if the
    side to move would lose at least THREAT_DROP points of winning chances by ignoring it.
    """
    passed = _passed_turn(board)
    if passed is None:
        return None
    result = engine.evaluate(passed)
    if not result["best_san"]:
        return None
    drop = _chances(white_now, board.turn) - _chances(result["white"], board.turn)
    if drop < THREAT_DROP:
        return None
    return result["best_san"], _describe(passed, passed.parse_san(result["best_san"]))


def _describe(board: chess.Board, move: chess.Move) -> str:
    """Describe a move in words, so the model doesn't have to decode chess notation."""
    piece = _PIECE_NAMES[board.piece_type_at(move.from_square)]
    origin, target = chess.square_name(move.from_square), chess.square_name(move.to_square)
    if board.is_castling(move):
        return "castling"
    captured = board.piece_type_at(move.to_square) or (chess.PAWN if board.is_en_passant(move) else None)
    text = f"{piece} from {origin} "
    text += f"captures the {_PIECE_NAMES[captured]} on {target}" if captured else f"to {target}"
    if move.promotion:
        text += f" and becomes a {_PIECE_NAMES[move.promotion]}"
    board_after = board.copy(stack=False)
    board_after.push(move)
    if board_after.is_checkmate():
        text += ", which is checkmate"
    elif board_after.is_check():
        text += ", giving check"
    return text


def line_is_close(lines: list[dict], line: dict) -> bool:
    """A line within 5 points of the best is a fair alternative."""
    return abs(lines[0]["white"] - line["white"]) <= 5


def build_facts(engine, moves, ply, you, question, opening, board_at) -> tuple[str, set[str]]:
    """Return the facts text and the set of moves the reply may safely mention."""
    board: chess.Board = board_at(ply)
    side = _side(board.turn)
    other = _side(not board.turn)
    # Pieces are written like "Nf3" in the facts, so those aren't invented moves.
    allowed: set[str] = set()
    for color in (chess.WHITE, chess.BLACK):
        allowed.update(_piece_list(board, color))

    facts = []
    if you in ("white", "black"):
        facts.append(f"The person asking is playing {you.capitalize()}.")
    facts.append(f"White's pieces: {', '.join(_piece_list(board, chess.WHITE))}.")
    facts.append(f"Black's pieces: {', '.join(_piece_list(board, chess.BLACK))}.")
    difference = _material(board, chess.WHITE) - _material(board, chess.BLACK)
    if difference:
        leader = "White" if difference > 0 else "Black"
        facts.append(f"{leader} is ahead on material by {abs(difference)} points.")
    else:
        facts.append("Material is level.")

    if opening:
        facts.append(f"Opening: {opening['name']}.")

    if ply > 0:
        before = board_at(ply - 1)
        played = chess.Move.from_uci(moves[ply - 1])
        before_eval = engine.evaluate(before)
        after_eval = engine.evaluate(board)
        rating = rate_move(
            before_eval["white"],
            after_eval["white"],
            before.turn == chess.WHITE,
            played.uci() == before_eval["best"],
        )
        san = before.san(played)
        allowed.add(_strip(san))
        line = (
            f"Last move: {san} by {_side(before.turn)}. {_side(before.turn)}'s winning chances "
            f"went from {round(rating['before'])}% to {round(rating['after'])}%"
        )
        if rating["rating"]:
            allowed.add(_strip(before_eval["best_san"]))
            line += f", which is rated a {rating['rating']}. Stockfish preferred {before_eval['best_san']}"
        facts.append(line + ".")

    outcome = board.outcome(claim_draw=True)
    if outcome is not None:
        result = "a draw" if outcome.winner is None else f"a win for {_side(outcome.winner)}"
        facts.append(f"The game is over: {result}.")
        return "\n".join(facts), allowed

    facts.append(f"It is {side}'s turn.")
    if you in ("white", "black") and you.capitalize() != side:
        facts.append(
            f"That is the opponent's turn, so the moves below are {side}'s options, not the "
            f"asker's. Never tell the asker to play them."
        )
    if board.is_check():
        facts.append(f"{side}'s king is in check.")
    lines = engine.top_lines(board)
    if lines:
        facts.append(f"{side}'s winning chances are about {_chances(lines[0]['white'], board.turn)}%.")
        for number, line in enumerate(lines):
            allowed.update(_strip(san) for san in line["sans"])
            label = "BEST MOVE" if number == 0 else "Another good option" if line_is_close(lines, line) else "A weaker option"
            first = board.parse_san(line["sans"][0])
            follow_up = f" Likely continuation: {' '.join(line['sans'][1:])}." if line["sans"][1:] else ""
            facts.append(
                f"{label}: {line['sans'][0]} ({_describe(board, first)}). {side}'s winning chances "
                f"after it are about {_chances(line['white'], board.turn)}%.{follow_up}"
            )

    now = engine.evaluate(board)
    threat = _threat(engine, board, now["white"])
    if threat:
        allowed.add(_strip(threat[0]))
        facts.append(f"{other}'s real threat, if {side} ignored it: {threat[0]} ({threat[1]}).")
    elif not board.is_check():
        facts.append(f"{other} has no serious threat right now.")
    for color in (board.turn, not board.turn):
        for note in _loose_pieces(board, color):
            facts.append(f"{_side(color)}'s {note}.")

    legal, illegal = mentioned_moves(board, question)
    for move in legal:
        san = board.san(move)
        allowed.add(_strip(san))
        after = board.copy()
        after.push(move)
        result = engine.evaluate(after)
        rating = rate_move(now["white"], result["white"], board.turn == chess.WHITE, move.uci() == now["best"])
        text = (
            f"The question asks about {san} ({_describe(board, move)}). If {side} plays it, "
            f"{side}'s winning chances go from "
            f"{round(rating['before'])}% to {round(rating['after'])}%"
        )
        text += f", which is a {rating['rating']}." if rating["rating"] else ", with no big change."
        if result["best_san"]:
            allowed.add(_strip(result["best_san"]))
            reply = after.parse_san(result["best_san"])
            text += f" {other}'s best reply is {result['best_san']} ({_describe(after, reply)})."
        facts.append(text)
    for token in illegal:
        allowed.add(_strip(token))  # the rules check covered this, so a warning isn't needed
        facts.append(f"The question mentions \"{token}\", which is not a legal move for {side} here.")
    return "\n".join(facts), allowed


def unverified_moves(reply: str, allowed: set[str]) -> list[str]:
    """Return moves named in a reply that the facts don't support."""
    found = []
    for token in [*_MOVE_IN_REPLY.findall(reply), *_PAWN_MOVE_IN_REPLY.findall(reply)]:
        if _strip(token) not in allowed and token not in found:
            found.append(token)
    return found


def build_messages(history: list[dict], facts: str) -> list[dict]:
    """Put the facts in the newest message only, since the board changes between questions."""
    question = history[-1]["content"]
    last = {
        "role": "user",
        "content": (
            f"POSITION FACTS:\n{facts}\n\nQUESTION: {question}\n\n"
            "If this is a general chess question rather than one about this position, ignore "
            "the facts and answer it from your chess knowledge."
        ),
    }
    return [{"role": "system", "content": SYSTEM_PROMPT}, *history[:-1], last]


def status() -> dict:
    try:
        response = httpx.get(f"{LLM_URL}/models", timeout=1.5)
        response.raise_for_status()
        return {"available": True, "model": response.json()["data"][0]["id"]}
    except (httpx.HTTPError, KeyError, IndexError, ValueError):
        return {"available": False, "model": None, "start_command": LLM_START_COMMAND}


def stream_reply(messages: list[dict]) -> Iterator[str]:
    # Reasoning stays off: in testing it took 35-50s per answer, sometimes returned nothing,
    # and wasn't clearly better for putting verified facts into words.
    body = {"messages": messages, "stream": True, "temperature": 0.2, "max_tokens": 500}
    timeout = httpx.Timeout(connect=2, read=120, write=10, pool=2)
    try:
        with httpx.stream("POST", f"{LLM_URL}/chat/completions", json=body, timeout=timeout) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                choices = json.loads(line[6:]).get("choices") or []
                delta = choices[0].get("delta", {}).get("content") if choices else None
                if delta:
                    yield delta
    except httpx.HTTPError as exc:
        raise LlmUnavailable(str(exc)) from exc
