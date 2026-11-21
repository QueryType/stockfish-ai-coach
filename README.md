# Chess Coach

A private chess coach for beginners that runs on your own computer, using the
[Stockfish](https://stockfishchess.org) engine and a local AI model. Play against a bot at a strength
that suits you, swap sides whenever you like, see how each move changed your winning
chances, and ask a local AI coach about the position in plain English.

Your games are saved in `data/games/`. The app doesn't send them anywhere.

## Quick start

```bash
./run.sh
```

Then open http://127.0.0.1:8765. Everything works straight away except **Ask the coach**,
which needs [the coach's model](#the-coachs-model) running too.

## Playing a game

| Option | What it does |
|---|---|
| **Moving a piece** | Click a piece then a square, or drag it. Legal moves are marked on the board. |
| **New game** | Choose White, who moves first, or Black. |
| **Coach strength** | Sets how well the bot plays. A change applies from its next move. |
| **Swap sides** | You take over the other side and the coach plays the side you left, with the board flipped. Use it to try the opposing position, or set strength to Full Stockfish to see how your position could be played. |
| **Play both sides** | Tick it to move White and Black yourself while the coach waits. Untick it to go back to playing the coach: you keep the side at the bottom of the board, and the coach moves straight away if it's its turn. Use **Swap sides** first if you want the other side. Ticking it while the coach is thinking cancels that move. |
| **Try again** | Appears if the coach's move fails, for example because the server was down. |

Pawns reaching the last rank always become queens. Games are saved automatically after
each move.

### Coach strength levels

| Level | How it plays |
|---|---|
| **Gentle** | Weakest. Often picks a random move. A good place to start. |
| **Easy** | Sometimes picks a random move, and otherwise accepts weaker moves. |
| **Medium** | Rarely picks a random move, and stays closer to the best moves. |
| **Club** | Stockfish's own limiter, at about 1320 Elo. |
| **Full Stockfish** | Full strength with a limit on how long it searches. |

Gentle, Easy and Medium are weakened on purpose and haven't been measured against Elo
ratings, so try them to see which suits you.

## Looking through a game

You can step through the moves at any time, even during a game:

- Click a move in the move list.
- Use the `⏮ ◀ ▶ ⏭` buttons under the board.
- Press the left and right arrow keys.

You can't move pieces while viewing an earlier position. Press `⏭` to return to the game.

## Analysis

| Option | What it does |
|---|---|
| **Auto-analyse each move** | On by default. Rates each move as soon as it's played. |
| **Show best move** | Off by default, so the best move isn't given away on your turn. Tick it to see the best move during play. |
| **Analyse** | Opens full analysis of the position on the board, including the best move. It works on any position, even before the first move. |
| **Continue game** | Leaves the analysis and returns to the latest position. |
| **Play from here** | Starts a new saved game from the position you're viewing, playing the side to move. The original game stays as it was. |

Choosing **Analyse** while the coach is thinking cancels that move. The coach moves again
when you choose **Continue game**.

### What the analysis shows

- **Move summary:** how the last move changed the winning chances, and what Stockfish
  preferred if it was a poor move.
- **Position summary:** whose turn it is and their winning chances.
- **Winning-chances graph:** shows the chances of the side you're playing, move by move.
  Hover to read a value, or click to jump to that position.
- **Your turning points:** up to 5 of your biggest mistakes. Click one to jump to it. With
  **Play both sides** ticked, this covers every move by either side.
- **Opening name:** appears under the status when the game matches one of about 3,900 known
  positions, even if it got there by a different move order.

| On the board or move list | Meaning |
|---|---|
| Blue arrow | The best move from this position. |
| Green arrow | What would have been better than the poor move just played. If it's the same piece, the arrow starts where that piece stood before the move. |
| `?!` | Inaccuracy |
| `?` | Mistake |
| `??` | Blunder |

### How moves are rated

A move's rating is how far it drops the winning chances of the side that played it:

| Drop | Rating |
|---|---|
| 10 or more points | Inaccuracy |
| 20 or more points | Mistake |
| 30 or more points | Blunder |

Winning chances are converted from Stockfish's score using the curve Lichess publishes.
They're an estimate, not an exact probability. Stockfish's top move is never marked as a
mistake.

## Ask the coach

Open the **Ask the coach** tab to ask about the position currently on the board.

- **Asking:** type a question and press `Enter`, or `Shift+Enter` for a new line.
  Questions can be up to 2,000 characters.
- **Suggested questions:** an empty chat offers four to tap, such as "What is my opponent
  threatening?"
- **Asking about a move:** write it as `Nf3`, `nf3`, `e4` or "knight to f3". Stockfish checks
  up to 3 moves per question.
- **General chess questions:** you can also ask about ideas and terms, like "What is a fork?"
- **Clear chat:** deletes the conversation and stops any answer still being written.

The coach remembers the latest 12 messages. Chat clears when you start a new game, open a
saved or pasted game, or use **Play from here** to start a new game from an earlier
position. **Continue game** keeps it.

### How far to trust it

For each question about the position, Stockfish and `python-chess` work out the facts: the
top moves, real threats, undefended pieces, whether a move is legal and what happens if
it's played. The model then puts those facts into words.

- **Reliable:** Stockfish's moves and winning chances, and whether a move is legal.
- **Less reliable:** the model's explanations. It can give a wrong reason for a correct
  move, so trust the numbers if the words disagree with them.
- **Warnings:** if a reply names a move Stockfish didn't check, a warning appears under it.
  The warning can't catch a wrong reason.
- **Best on your turn:** questions are answered for whoever's turn it is on the board. "Is
  Qh5 good?" during your opponent's turn is read as a question about their queen. With **Play
  both sides** ticked, "you" means whichever side is to move.
- **General answers:** these come from the model's own knowledge and aren't checked.

### The coach's model

Chat needs a local model server with an OpenAI-compatible API, such as
[llama.cpp](https://github.com/ggml-org/llama.cpp)'s `llama-server`, LM Studio or Ollama.
Start it in a separate terminal, for example:

```bash
llama-server --model /path/to/model.gguf --host 127.0.0.1 --port 8080
```

It was developed with Qwen3.6-35B-A3B (Q4_K_M), which uses about 26 GB of memory. Smaller
models work too, with weaker explanations. If your server isn't on port 8080, set `LLM_URL`.
The chat tab shows a start command if the model isn't running; set `LLM_START_COMMAND` to
show your own.

Thinking mode stays off: in testing it took 35–50 seconds per answer, sometimes returned
nothing and wasn't clearly better.

## Saved games and pasting

| Option | What it does |
|---|---|
| **Saved games** | Lists your games, newest first, and opens one for analysis. Use **Continue game** to keep playing an unfinished one. |
| **Delete** (in Saved games) | Removes a saved game for good, after asking you to confirm. The game you have open is marked **Open now** and can't be deleted, because it saves itself after every move. Start a new game or open another one first. |
| **Paste a game or position** | Paste a whole game (PGN) to analyse it, or one position (FEN) to play from it as the side to move. |

- Refreshing the page starts a new game. The previous one stays in **Saved games**.
- A pasted PGN isn't saved unless you continue playing from it, which creates a new game.
- A game from a pasted FEN saves once a move has been played.

Your coach strength and the three checkboxes are remembered separately in each browser, so
if **Play both sides** is ticked, a new game starts with the coach waiting.

## Settings

Set these when starting the app, for example `PORT=9000 ./run.sh`.

| Setting | Default | What it does |
|---|---|---|
| `HOST` | `127.0.0.1` | The address to listen on. |
| `PORT` | `8765` | The port to listen on. |
| `LLM_URL` | `http://127.0.0.1:8080/v1` | Any OpenAI-compatible model server. |
| `CHESS_COACH_DATA` | `data` | Where saved games are stored. |
| `LLM_START_COMMAND` | a `llama-server` example | The command the chat tab shows when the model isn't running. |

**Network access:** the app has no login. With a network address, or `HOST=0.0.0.0`, anyone
on your network can play, ask the coach and see your saved games. Use the default address
if you only need it on this computer.

## Setup from scratch

```bash
brew install stockfish          # macOS
# sudo apt install stockfish    # Debian/Ubuntu
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

`run.sh` uses `.venv` directly. To run Python commands yourself, activate it first:

```bash
source .venv/bin/activate
```

## Tests

```bash
.venv/bin/python -m pytest tests
```

The tests run against the real Stockfish, but they replace the model with a stand-in, so
they don't need it running.

## Licence

This project is licensed under GPL-3.0-or-later (see `LICENSE`). The board (`web/vendor/`)
is [Chessground](https://github.com/lichess-org/chessground), also GPL-3.0-or-later.
Stockfish is GPL-3.0 and is installed separately, not bundled. Opening names (`app/openings.tsv`) come from
[lichess-org/chess-openings](https://github.com/lichess-org/chess-openings), which is CC0.
