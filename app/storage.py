"""Save games as one JSON file each. Nothing leaves this computer."""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

ID_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")


class Store:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _path(self, game_id: str) -> Path:
        if not ID_RE.match(game_id):
            raise ValueError("Bad game id.")
        return self.directory / f"{game_id}.json"

    def save(self, game_id: str, game: dict) -> None:
        path = self._path(game_id)
        self.directory.mkdir(parents=True, exist_ok=True)
        # Write then rename, so a crash can't leave half a game behind.
        fd, tmp = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump({**game, "id": game_id}, handle)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def load(self, game_id: str) -> dict | None:
        path = self._path(game_id)
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def delete(self, game_id: str) -> bool:
        path = self._path(game_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def list(self) -> list[dict]:
        if not self.directory.exists():
            return []
        files = sorted(self.directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        games = []
        for path in files:
            try:
                game = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue  # one damaged file shouldn't hide the other games
            games.append(
                {
                    "id": game.get("id", path.stem),
                    "created_at": game.get("created_at"),
                    "result": game.get("result", "*"),
                    "you": game.get("you"),
                    "level": game.get("level"),
                    "move_count": len(game.get("moves", [])),
                }
            )
        return games
