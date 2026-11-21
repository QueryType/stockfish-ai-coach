"""The local web server. Run it with ./run.sh, then open http://127.0.0.1:8765."""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import chess
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import chessops, coach
from .analysis import rate_move
from .bot import LEVELS
from .engine import Engine
from .storage import Store

ROOT = Path(__file__).resolve().parent.parent
MAX_CHAT_MESSAGES = 12  # keeps long chats quick to answer
store = Store(Path(os.environ.get("CHESS_COACH_DATA", ROOT / "data")) / "games")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.engine = Engine()
    yield
    app.state.engine.close()


app = FastAPI(title="Chess Coach", lifespan=lifespan)


@app.exception_handler(ValueError)
async def bad_input(_: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


class Line(BaseModel):
    start_fen: str = chess.STARTING_FEN
    moves: list[str] = Field(default_factory=list)


class MoveRequest(Line):
    uci: str


class BotRequest(Line):
    level: str


class AnalyzeRequest(Line):
    from_ply: int = Field(default=0, ge=0)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class ChatRequest(Line):
    ply: int = Field(ge=0)
    you: str | None = None
    messages: list[ChatMessage] = Field(min_length=1, max_length=40)


class PgnRequest(BaseModel):
    pgn: str


class Game(Line):
    created_at: str
    result: str = "*"
    you: str = "white"
    level: str = "gentle"
    human: list[bool] = Field(default_factory=list)


@app.get("/api/levels")
def levels() -> list[dict]:
    return [{"id": key, "label": level.label} for key, level in LEVELS.items()]


@app.post("/api/position")
def position(line: Line) -> dict:
    return chessops.position_info(line.start_fen, line.moves)


@app.post("/api/move")
def move(request: MoveRequest) -> dict:
    uci = chessops.normalize_move(request.start_fen, request.moves, request.uci)
    return {"uci": uci, **chessops.position_info(request.start_fen, [*request.moves, uci])}


@app.post("/api/bot-move")
def bot_move(request: BotRequest, http: Request) -> dict:
    level = LEVELS.get(request.level)
    if level is None:
        raise HTTPException(400, "Unknown level.")
    board = chessops.replay(request.start_fen, request.moves)
    if chessops.status(board)["over"]:
        raise HTTPException(400, "The game is already over.")
    uci = http.app.state.engine.bot_move(board, level).uci()
    return {"uci": uci, **chessops.position_info(request.start_fen, [*request.moves, uci])}


@app.post("/api/analyze")
def analyze(line: AnalyzeRequest, http: Request) -> StreamingResponse:
    """Stream one JSON line per position, starting at from_ply, so the graph fills in live."""
    chessops.replay(line.start_fen, line.moves)  # reject bad input before streaming
    engine: Engine = http.app.state.engine

    def stream():
        if line.from_ply > len(line.moves):
            return
        # Rating a move needs the position before it, so start one position early.
        start = max(line.from_ply - 1, 0)
        board = chessops.replay(line.start_fen, line.moves[:start])
        previous = engine.evaluate(board)
        if line.from_ply == 0:
            yield json.dumps(
                {
                    "ply": 0,
                    "white": previous["white"],
                    "next_best": previous["best"],
                    "next_best_san": previous["best_san"],
                }
            ) + "\n"
        for index, uci in enumerate(line.moves[start:], start):
            move_obj = chess.Move.from_uci(uci)
            mover_is_white = board.turn == chess.WHITE
            san = board.san(move_obj)
            board.push(move_obj)
            current = engine.evaluate(board)
            rating = rate_move(
                previous["white"], current["white"], mover_is_white, uci == previous["best"]
            )
            yield json.dumps(
                {
                    "ply": index + 1,
                    "white": current["white"],
                    "san": san,
                    "best": previous["best"],
                    "best_san": previous["best_san"],
                    "next_best": current["best"],
                    "next_best_san": current["best_san"],
                    **rating,
                }
            ) + "\n"
            previous = current

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@app.get("/api/chat/status")
def chat_status() -> dict:
    return coach.status()


@app.post("/api/chat")
def chat(request: ChatRequest, http: Request) -> StreamingResponse:
    """Work out the facts for the position on the board, then stream the coach's reply."""
    if request.ply > len(request.moves):
        raise ValueError("That position isn't part of this game.")
    if request.messages[-1].role != "user":
        raise ValueError("The last message must be a question.")
    chessops.replay(request.start_fen, request.moves)
    history = [message.model_dump() for message in request.messages[-MAX_CHAT_MESSAGES:]]

    facts, allowed = coach.build_facts(
        http.app.state.engine,
        request.moves,
        request.ply,
        request.you,
        history[-1]["content"],
        chessops.history(request.start_fen, request.moves[: request.ply])[-1]["opening"],
        lambda ply: chessops.replay(request.start_fen, request.moves[:ply]),
    )

    def stream():
        parts = []
        try:
            for delta in coach.stream_reply(coach.build_messages(history, facts)):
                parts.append(delta)
                yield json.dumps({"delta": delta}) + "\n"
        except coach.LlmUnavailable:
            yield json.dumps(
                {"error": "The coach's model isn't running.", "start_command": coach.LLM_START_COMMAND}
            ) + "\n"
            return
        unverified = coach.unverified_moves("".join(parts), allowed)
        yield json.dumps({"done": True, "unverified": unverified}) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@app.post("/api/pgn")
def pgn(request: PgnRequest) -> dict:
    return chessops.parse_pgn(request.pgn)


@app.get("/api/games")
def list_games() -> list[dict]:
    return store.list()


@app.get("/api/games/{game_id}")
def load_game(game_id: str) -> dict:
    game = store.load(game_id)
    if game is None:
        raise HTTPException(404, "Game not found.")
    return game


@app.put("/api/games/{game_id}")
def save_game(game_id: str, game: Game) -> dict:
    chessops.replay(game.start_fen, game.moves)
    store.save(game_id, game.model_dump())
    return {"id": game_id}


@app.delete("/api/games/{game_id}")
def delete_game(game_id: str) -> dict:
    if not store.delete(game_id):
        raise HTTPException(404, "Game not found.")
    return {"deleted": game_id}


class AlwaysCheckedFiles(StaticFiles):
    """Make the browser check for a newer copy, so an update can't leave it running old code."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/", AlwaysCheckedFiles(directory=ROOT / "web", html=True), name="web")
