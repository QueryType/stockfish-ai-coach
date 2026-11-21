import { Chessground } from "./vendor/chessground.min.js";

const STARTING_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const RATING_LABEL = { blunder: "Blunder", mistake: "Mistake", inaccuracy: "Inaccuracy" };
const RATING_MARK = { blunder: "??", mistake: "?", inaccuracy: "?!" };
const RATING_RADIUS = { blunder: 6.5, mistake: 5.5, inaccuracy: 4.5 };

const $ = (id) => document.getElementById(id);

const state = {
  id: null,
  createdAt: null,
  startFen: STARTING_FEN,
  moves: [],
  human: [],
  you: "white",
  mode: "play", // "play" or "review"
  saveable: true,
  info: null, // the server's view of the whole line
  cursor: 0,
  rows: [], // analysis, indexed by ply
  thinking: false,
  coachFailed: false, // the coach's move failed, so offer to try again
  error: null,
  auto: true, // analyse each move as it's played
  showBest: false, // show the best move on your own turn while playing
  bothSides: false, // you move both sides, and the coach waits
  tab: "analysis",
  chat: [], // {role, content, unverified?, error?}
  chatBusy: false,
};

let requestToken = 0; // lets a new game ignore a reply meant for the old one
let analysisController = null; // the analysis running now, if any
let analysisQueued = false;
let chatController = null;

// crypto.randomUUID only exists on HTTPS and localhost, and this app can run on a plain
// HTTP network address. getRandomValues works everywhere.
function newGameId() {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

// --- Server calls -----------------------------------------------------------

class NetworkError extends Error {}

async function post(path, body) {
  let response;
  try {
    response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new NetworkError("Can't reach the app's server. Check that it's still running.");
  }
  return readJson(response);
}

// Asking for a bot move changes nothing on the server, so it's safe to ask again. This
// covers brief drops, such as the first request after the server restarts.
async function postWithRetries(path, body, delays = [500, 1500]) {
  for (const delay of delays) {
    try {
      return await post(path, body);
    } catch (error) {
      if (!(error instanceof NetworkError)) throw error;
      await new Promise((resolve) => setTimeout(resolve, delay));
    }
  }
  return post(path, body);
}

async function readJson(response) {
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail : "The request failed.";
    throw new Error(detail);
  }
  return data;
}

const line = () => ({ start_fen: state.startFen, moves: state.moves });

function saveGame() {
  if (!state.saveable || state.moves.length === 0) return;
  const body = {
    ...line(),
    created_at: state.createdAt,
    result: state.info?.status.result ?? "*",
    you: state.you,
    level: $("level").value,
    human: state.human,
  };
  fetch(`/api/games/${state.id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then(readJson).catch((error) => showError(`Couldn't save this game: ${error.message}`));
}

function showError(message) {
  state.error = message;
  renderStatus();
}

// --- Board ------------------------------------------------------------------

const cg = Chessground($("board"), {
  fen: STARTING_FEN,
  coordinates: true,
  premovable: { enabled: false },
  highlight: { lastMove: true, check: true },
  animation: { enabled: true, duration: 180 },
  drawable: { enabled: false, visible: true },
  movable: { free: false, color: undefined, showDests: true, events: { after: onUserMove } },
});
new ResizeObserver(() => document.body.dispatchEvent(new Event("chessground.resize"))).observe($("board"));
new ResizeObserver(() => {
  if (state.mode === "review" && state.info) renderChart();
}).observe($("chart"));

function isYourTurn() {
  return state.mode === "play" && !state.thinking && !state.info.status.over
    && (state.bothSides || state.info.turn === state.you);
}

function renderBoard() {
  const position = state.info.history[state.cursor];
  const last = state.cursor > 0 ? state.moves[state.cursor - 1] : null;
  const canMove = isYourTurn() && state.cursor === state.moves.length;
  cg.set({
    fen: position.fen,
    orientation: state.you,
    turnColor: position.turn,
    check: position.check ? position.turn : false,
    lastMove: last ? [last.slice(0, 2), last.slice(2, 4)] : undefined,
    movable: {
      color: canMove ? position.turn : undefined,
      dests: canMove ? new Map(Object.entries(state.info.dests)) : new Map(),
    },
  });
  renderArrows();
}

// While playing, the best move stays hidden unless you ask for it, so it isn't a spoiler.
const revealBest = () => state.mode === "review" || state.showBest;

function renderArrows() {
  const row = state.rows[state.cursor];
  const shapes = [];
  if (analysisVisible() && row) {
    if (row.rating && row.best) {
      shapes.push({ orig: row.best.slice(0, 2), dest: row.best.slice(2, 4), brush: "green" });
    }
    if (row.next_best && revealBest()) {
      shapes.push({ orig: row.next_best.slice(0, 2), dest: row.next_best.slice(2, 4), brush: "blue" });
    }
  }
  cg.setAutoShapes(shapes);
}

// --- Playing ----------------------------------------------------------------

function applyLine(info, moves, human) {
  state.moves = moves;
  state.human = human;
  state.info = info;
  state.cursor = moves.length;
}

async function onUserMove(orig, dest) {
  const token = ++requestToken;
  state.error = null;
  try {
    const result = await post("/api/move", { ...line(), uci: orig + dest });
    if (token !== requestToken) return;
    applyLine(result, [...state.moves, result.uci], [...state.human, true]);
    saveGame();
    render();
    if (state.auto) syncAnalysis();
    await botTurnIfNeeded();
  } catch (error) {
    if (token !== requestToken) return;
    state.error = error.message;
    render();
  }
}

async function botTurnIfNeeded() {
  if (state.mode !== "play" || state.bothSides || state.info.status.over || state.info.turn === state.you) return;
  const token = ++requestToken;
  state.thinking = true;
  state.coachFailed = false;
  state.error = null;
  render();
  try {
    const result = await postWithRetries("/api/bot-move", { ...line(), level: $("level").value });
    if (token !== requestToken) return;
    applyLine(result, [...state.moves, result.uci], [...state.human, false]);
    state.thinking = false;
    saveGame();
    if (state.auto) syncAnalysis();
  } catch (error) {
    if (token !== requestToken) return;
    state.thinking = false;
    state.coachFailed = true;
    state.error = `The coach couldn't move. ${error.message}`;
  }
  render();
}

async function startGame({ startFen = STARTING_FEN, moves = [], human = [], you, id = null, createdAt = null, rows = [] }) {
  const token = ++requestToken;
  cancelAnalysis();
  const info = await post("/api/position", { start_fen: startFen, moves });
  if (token !== requestToken) return;
  if (!id || id !== state.id) clearChat();
  Object.assign(state, {
    id: id ?? newGameId(),
    createdAt: createdAt ?? new Date().toISOString(),
    startFen,
    you: you ?? info.turn,
    mode: "play",
    saveable: true,
    rows,
    thinking: false,
    coachFailed: false,
    error: null,
  });
  applyLine(info, moves, human);
  render();
  if (state.auto) syncAnalysis();
  await botTurnIfNeeded();
}

async function swapSides() {
  state.you = state.you === "white" ? "black" : "white";
  state.cursor = state.moves.length;
  state.coachFailed = false;
  state.error = null;
  saveGame();
  render();
  await botTurnIfNeeded();
}

function setBothSides() {
  state.error = null;
  state.coachFailed = false;
  if (state.bothSides && state.thinking) {
    ++requestToken; // ignore the move the coach was about to make
    state.thinking = false;
  }
  render();
  return botTurnIfNeeded();
}

// --- Reviewing --------------------------------------------------------------

function enterReview({ cursor = 0 } = {}) {
  ++requestToken; // ignore any reply still on its way
  state.mode = "review";
  state.thinking = false;
  state.coachFailed = false;
  state.cursor = cursor;
  state.error = null;
  render();
  syncAnalysis();
}

function cancelAnalysis() {
  analysisController?.abort();
  analysisController = null;
  analysisQueued = false;
}

// Read a stream of JSON lines, calling onRow for each one.
async function readStream(response, onRow) {
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    const parts = buffer.split("\n");
    buffer = parts.pop();
    for (const part of parts) {
      if (part) onRow(JSON.parse(part));
    }
  }
}

function firstMissingRow() {
  for (let ply = 0; ply <= state.moves.length; ply += 1) {
    if (!state.rows[ply]) return ply;
  }
  return null;
}

// Analyse only the positions that haven't been analysed yet.
async function syncAnalysis() {
  if (analysisController) {
    analysisQueued = true; // a move arrived mid-analysis, so go round again afterwards
    return;
  }
  const controller = new AbortController();
  analysisController = controller;
  analysisQueued = false;
  try {
    let fromPly = firstMissingRow();
    while (fromPly !== null && !controller.signal.aborted) {
      analysisQueued = false;
      const response = await fetch("/api/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...line(), from_ply: fromPly }),
        signal: controller.signal,
      });
      if (!response.ok) await readJson(response);
      await readStream(response, (row) => {
        if (controller.signal.aborted) return;
        state.rows[row.ply] = row;
        renderAnalysis();
      });
      fromPly = firstMissingRow();
    }
  } catch (error) {
    if (error.name !== "AbortError") showError(`The analysis stopped early: ${error.message}`);
  } finally {
    if (analysisController === controller) analysisController = null;
    if (analysisQueued && !controller.signal.aborted) syncAnalysis();
  }
}

function setCursor(ply) {
  if (!state.info) return;
  state.cursor = Math.max(0, Math.min(state.moves.length, ply));
  render();
}
async function continueGame() {
  state.cursor = state.moves.length;
  await playFromHere();
}

async function playFromHere() {
  const atEnd = state.cursor === state.moves.length;
  const moves = state.moves.slice(0, state.cursor);
  // Keep the existing game when continuing it. A branch becomes a new saved game.
  await startGame({
    startFen: state.startFen,
    moves,
    human: paddedHuman().slice(0, state.cursor),
    id: atEnd && state.saveable ? state.id : null,
    createdAt: atEnd && state.saveable ? state.createdAt : null,
    you: atEnd && state.saveable ? state.you : undefined,
    rows: state.rows.slice(0, state.cursor + 1), // these positions are unchanged
  });
}

function paddedHuman() {
  return state.moves.map((_, index) => Boolean(state.human[index]));
}

// --- Words ------------------------------------------------------------------

function moveLabel(ply) {
  const fullMove = fenFullMove(state.info.history[ply - 1].fen);
  const dots = state.info.history[ply - 1].turn === "white" ? "." : "...";
  return `${fullMove}${dots} ${state.info.sans[ply - 1]}`;
}

function fenFullMove(fen) {
  return Number(fen.split(" ")[5]);
}

function hasPlayers() {
  return state.human.some(Boolean);
}

// When you're moving both sides, "you" and "the coach" don't say which side is meant.
function namesPlayers() {
  return hasPlayers() && !state.bothSides;
}

function moverName(ply) {
  if (namesPlayers()) return state.human[ply - 1] ? "you" : "the coach";
  return state.info.history[ply - 1].turn === "white" ? "White" : "Black";
}

function moverPossessive(ply) {
  const name = moverName(ply);
  if (name === "you") return "your";
  if (name === "the coach") return "the coach's";
  return `${name}'s`;
}

function capitalize(text) {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function statusText() {
  if (state.error) return state.error;
  const { status, turn } = state.info;
  if (state.mode === "review") {
    const done = state.rows.filter(Boolean).length;
    const total = state.moves.length + 1;
    if (done < total) return `Analysing… ${done} of ${total} positions`;
    return "Analysis finished. Step through the moves, or click the graph.";
  }
  if (state.cursor < state.moves.length) {
    return "You're looking at an earlier position. Press ⏭ to return to the game.";
  }
  if (status.over) {
    if (status.result === "1/2-1/2") return `Draw: ${status.reason.toLowerCase()}.`;
    const whiteWon = status.result === "1-0";
    if (state.bothSides) return `${status.reason}. ${whiteWon ? "White" : "Black"} won.`;
    const youWon = whiteWon === (state.you === "white");
    return `${status.reason}. ${youWon ? "You won!" : "The coach won."}`;
  }
  if (state.thinking) return "The coach is thinking…";
  const side = turn === "white" ? "White" : "Black";
  if (state.bothSides) {
    const coachSide = state.you === "white" ? "Black" : "White";
    return `${side} to move. You're moving both sides. Untick the box and the coach plays ${coachSide}.`;
  }
  return `Your move. You're playing ${side}.`;
}

// --- Rendering --------------------------------------------------------------

function renderStatus() {
  $("status").textContent = statusText();
  $("status").classList.toggle("error", Boolean(state.error));
}

const analysisVisible = () => state.mode === "review" || state.auto;

function renderOpening() {
  const box = $("opening");
  const opening = state.info.history[state.cursor].opening;
  box.hidden = !opening;
  box.replaceChildren();
  if (!opening) return;
  const name = document.createElement("strong");
  name.textContent = opening.name;
  box.append("Opening: ", name, ` (${opening.eco})`);
}

function renderTabs() {
  for (const [name, button, panel] of [["analysis", "tab-analysis", "analysis-tab"], ["chat", "tab-chat", "chat-tab"]]) {
    $(button).setAttribute("aria-selected", String(state.tab === name));
    $(panel).hidden = state.tab !== name;
  }
}

function renderControls() {
  const reviewing = state.mode === "review";
  $("play-controls").hidden = reviewing;
  $("review-controls").hidden = !reviewing;
  $("review-panel").hidden = !analysisVisible();
  $("swap").disabled = state.thinking || state.info.status.over;
  $("retry").hidden = !state.coachFailed;
  $("nav-start").disabled = $("nav-prev").disabled = state.cursor === 0;
  $("nav-end").disabled = $("nav-next").disabled = state.cursor === state.moves.length;
}

function renderMoves() {
  const list = $("moves");
  list.replaceChildren();
  const firstFull = fenFullMove(state.info.history[0].fen);
  let current = null;

  const addNumber = (number) => {
    const item = document.createElement("li");
    item.className = "number";
    item.textContent = `${number}.`;
    list.append(item);
  };
  const addBlank = () => list.append(document.createElement("li"));

  state.info.sans.forEach((san, index) => {
    const ply = index + 1;
    const isWhite = state.info.history[index].turn === "white";
    if (index === 0 && !isWhite) {
      addNumber(firstFull);
      addBlank();
    } else if (isWhite) {
      addNumber(fenFullMove(state.info.history[index].fen));
    }
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = san;
    const row = state.rows[ply];
    if (row?.rating) {
      const note = document.createElement("span");
      note.className = `note ${row.rating}`;
      note.textContent = RATING_MARK[row.rating];
      button.append(note);
      button.title = RATING_LABEL[row.rating];
    }
    button.addEventListener("click", () => setCursor(ply));
    if (ply === state.cursor) {
      button.classList.add("current");
      current = button;
    }
    item.append(button);
    list.append(item);
  });
  // Scroll only the list, so the page doesn't jump while you're reading something else.
  if (current) list.scrollTop = current.offsetTop - list.clientHeight / 2;
}

function renderExplanation() {
  const box = $("explain");
  box.replaceChildren();
  const title = document.createElement("strong");
  const text = document.createElement("p");
  const now = document.createElement("p");
  box.append(title, text, now);
  const row = state.rows[state.cursor];
  now.textContent = positionSummary(row);
  if (state.cursor === 0) {
    title.textContent = "Starting position";
    text.textContent = "Use the arrows or click a move to step through the game.";
    return;
  }
  const label = moveLabel(state.cursor);
  if (!row) {
    title.textContent = label;
    text.textContent = "Still checking this move…";
    return;
  }
  const who = moverPossessive(state.cursor);
  const change = `${capitalize(who)} winning chances went from ${Math.round(row.before)}% to ${Math.round(row.after)}%.`;
  if (row.rating) {
    title.textContent = `${label}: ${RATING_LABEL[row.rating]}`;
    const samePiece = row.best.slice(0, 2) === state.moves[state.cursor - 1].slice(0, 2);
    const arrow = samePiece
      ? "The green arrow starts where that piece stood before this move."
      : "It's shown by the green arrow.";
    text.textContent = `${change} Stockfish preferred ${row.best_san}. ${arrow}`;
  } else {
    title.textContent = `${label}: fine`;
    text.textContent = change;
  }
}

function positionSummary(row) {
  if (!row) return "Checking this position…";
  if (!row.next_best) return "The game ended here.";
  const turn = state.info.history[row.ply].turn;
  const side = turn === "white" ? "White" : "Black";
  const chances = turn === "white" ? row.white : 100 - row.white;
  const summary = `Now it's ${side}'s turn, with ${Math.round(chances)}% winning chances.`;
  return revealBest() ? `${summary} The best move is ${row.next_best_san} (blue arrow).` : summary;
}

function renderTurningPoints() {
  const box = $("turning-points");
  box.replaceChildren();
  if (state.moves.length === 0) return;
  const candidates = state.rows
    .filter((row) => row && row.rating)
    .filter((row) => !namesPlayers() || state.human[row.ply - 1]);
  const worst = [...candidates].sort((a, b) => b.loss - a.loss).slice(0, 5).sort((a, b) => a.ply - b.ply);
  const heading = document.createElement("h2");
  heading.textContent = namesPlayers() ? "Your turning points" : "Turning points";
  box.append(heading);
  const finished = state.rows.filter(Boolean).length === state.moves.length + 1;
  if (worst.length === 0) {
    const note = document.createElement("p");
    note.textContent = finished ? "No big swings here. Nice steady play." : "Looking for them…";
    box.append(note);
    return;
  }
  for (const row of worst) {
    const button = document.createElement("button");
    button.type = "button";
    const name = document.createElement("span");
    name.textContent = `${moveLabel(row.ply)}: ${RATING_LABEL[row.rating]}`;
    const drop = document.createElement("span");
    drop.className = "drop";
    drop.textContent = `−${Math.round(row.loss)}%`;
    button.append(name, drop);
    button.addEventListener("click", () => setCursor(row.ply));
    box.append(button);
  }
}

// --- Chart ------------------------------------------------------------------

const SVG_NS = "http://www.w3.org/2000/svg";

function svgEl(name, attrs = {}, parent = null) {
  const element = document.createElementNS(SVG_NS, name);
  for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, value);
  parent?.append(element);
  return element;
}

function chancesFor(row) {
  return state.you === "white" ? row.white : 100 - row.white;
}

function renderChart() {
  const host = $("chart");
  host.replaceChildren();
  const side = state.you === "white" ? "White" : "Black";
  $("chart-title").textContent = `${side}'s winning chances`;
  const width = host.clientWidth;
  const height = host.clientHeight;
  if (!width) return;
  const margin = { top: 8, right: 8, bottom: 20, left: 36 };
  const plotW = width - margin.left - margin.right;
  const plotH = height - margin.top - margin.bottom;
  const total = Math.max(state.moves.length, 1);
  const xAt = (ply) => margin.left + (ply / total) * plotW;
  const yAt = (value) => margin.top + (1 - value / 100) * plotH;
  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": `${side}'s winning chances by move. The move list has the same information.` }, host);

  for (const tick of [0, 50, 100]) {
    svgEl("line", { x1: margin.left, x2: width - margin.right, y1: yAt(tick), y2: yAt(tick), class: tick === 50 ? "midline" : "gridline" }, svg);
    const text = svgEl("text", { x: margin.left - 6, y: yAt(tick) + 4, "text-anchor": "end" }, svg);
    text.textContent = `${tick}%`;
  }
  const firstFull = fenFullMove(state.info.history[0].fen);
  const lastFull = fenFullMove(state.info.history.at(-1).fen);
  const start = svgEl("text", { x: margin.left, y: height - 5 }, svg);
  start.textContent = `Move ${firstFull}`;
  const end = svgEl("text", { x: width - margin.right, y: height - 5, "text-anchor": "end" }, svg);
  end.textContent = `Move ${lastFull}`;

  const known = state.rows.filter(Boolean);
  if (known.length) {
    const path = known.map((row, index) => `${index ? "L" : "M"}${xAt(row.ply).toFixed(1)} ${yAt(chancesFor(row)).toFixed(1)}`).join("");
    svgEl("path", { d: path, class: "series" }, svg);
  }
  svgEl("line", { x1: xAt(state.cursor), x2: xAt(state.cursor), y1: margin.top, y2: margin.top + plotH, class: "cursor-line" }, svg);
  for (const row of known) {
    if (!row.rating) continue;
    if (namesPlayers() && !state.human[row.ply - 1]) continue;
    svgEl("circle", { cx: xAt(row.ply), cy: yAt(chancesFor(row)), r: RATING_RADIUS[row.rating], class: "marker", fill: `var(--${row.rating})` }, svg);
  }

  const hoverLine = svgEl("line", { y1: margin.top, y2: margin.top + plotH, class: "hover-line", visibility: "hidden" }, svg);
  const tooltip = $("tooltip");
  const plyAt = (event) => {
    const box = svg.getBoundingClientRect();
    return Math.max(0, Math.min(state.moves.length, Math.round(((event.clientX - box.left - margin.left) / plotW) * total)));
  };
  const hit = svgEl("rect", { x: 0, y: 0, width, height, fill: "transparent" }, svg);
  hit.addEventListener("pointermove", (event) => {
    const ply = plyAt(event);
    const row = state.rows[ply];
    hoverLine.setAttribute("x1", xAt(ply));
    hoverLine.setAttribute("x2", xAt(ply));
    hoverLine.setAttribute("visibility", "visible");
    if (!row) {
      tooltip.hidden = true;
      return;
    }
    tooltip.replaceChildren();
    const value = document.createElement("strong");
    value.textContent = `${Math.round(chancesFor(row))}%`;
    const detail = document.createElement("span");
    const label = ply === 0 ? "Start" : moveLabel(ply);
    detail.textContent = row.rating ? `${label}: ${RATING_LABEL[row.rating]}` : label;
    tooltip.append(value, detail);
    tooltip.hidden = false;
    const cardWidth = tooltip.parentElement.clientWidth;
    const left = xAt(ply) + 12;
    tooltip.style.left = `${Math.min(left, cardWidth - tooltip.offsetWidth - 4)}px`;
  });
  hit.addEventListener("pointerleave", () => {
    hoverLine.setAttribute("visibility", "hidden");
    tooltip.hidden = true;
  });
  hit.addEventListener("click", (event) => setCursor(plyAt(event)));
}

// Redraw everything except the pieces, so new analysis can't interrupt a move you're making.
function renderAnalysis() {
  renderStatus();
  renderMoves();
  renderArrows();
  renderChatContext();
  if (analysisVisible()) {
    renderExplanation();
    renderChart();
    renderTurningPoints();
  }
}

function render() {
  renderOpening();
  renderTabs();
  renderControls();
  renderBoard();
  renderAnalysis();
}

// --- Chat -------------------------------------------------------------------

const SUGGESTIONS = [
  "What should I play next, and why?",
  "Why was the last move good or bad?",
  "What is my opponent threatening?",
  "Who's better, and why?",
];

function clearChat() {
  chatController?.abort();
  chatController = null;
  state.chat = [];
  state.chatBusy = false;
  renderChatLog();
}

function renderChatContext() {
  const about = state.cursor === 0 ? "the starting position" : `the position after ${moveLabel(state.cursor)}`;
  $("chat-about").textContent = `Questions are about what's on the board: ${about}.`;
  $("chat-send").disabled = state.chatBusy;
}

function chatBubble(message) {
  const bubble = document.createElement("div");
  bubble.className = `bubble ${message.error ? "error" : message.role === "user" ? "user" : "coach"}`;
  // The model sometimes adds markdown bold despite being told not to.
  bubble.textContent = message.content.replace(/\*\*/g, "");
  if (message.startCommand) {
    const code = document.createElement("code");
    code.className = "caution";
    code.textContent = message.startCommand;
    bubble.append(code);
  }
  if (message.unverified?.length) {
    const caution = document.createElement("span");
    caution.className = "caution";
    caution.textContent = `Stockfish didn't check ${message.unverified.join(", ")}, so don't rely on what the coach says about ${message.unverified.length > 1 ? "them" : "it"}.`;
    bubble.append(caution);
  }
  return bubble;
}

function renderChatLog() {
  const log = $("chat-log");
  log.replaceChildren(...state.chat.map(chatBubble));
  log.scrollTop = log.scrollHeight;
  $("chat-suggestions").hidden = state.chat.length > 0;
  $("chat-clear").hidden = state.chat.length === 0;
}

async function askCoach(question) {
  if (state.chatBusy || !question.trim() || question.length > 2000) return;
  const controller = new AbortController();
  chatController = controller;
  state.chatBusy = true;
  state.chat.push({ role: "user", content: question.trim() });
  const history = state.chat
    .filter((message) => !message.error)
    .map(({ role, content }) => ({ role, content: content.slice(0, 4000) }));
  const reply = { role: "assistant", content: "" };
  state.chat.push(reply);
  reply.content = "Checking the position…";
  renderChatLog();
  renderChatContext();
  let started = false;
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...line(),
        ply: state.cursor,
        you: namesPlayers() ? state.you : null,
        messages: history.slice(-12),
      }),
      signal: controller.signal,
    });
    if (!response.ok) await readJson(response);
    await readStream(response, (part) => {
      if (controller.signal.aborted) return;
      if (part.delta) {
        reply.content = started ? reply.content + part.delta : part.delta;
        started = true;
      } else if (part.error) {
        reply.error = true;
        reply.content = part.error;
        reply.startCommand = part.start_command;
      } else if (part.done) {
        reply.unverified = part.unverified;
      }
      renderChatLog();
    });
    if (!started && !reply.error) {
      reply.error = true;
      reply.content = "The coach didn't give an answer. Please try again.";
    }
  } catch (error) {
    if (error.name === "AbortError") return;
    reply.error = true;
    reply.content = `Couldn't get an answer: ${error.message}`;
  }
  if (controller.signal.aborted) return;
  state.chatBusy = false;
  chatController = null;
  renderChatLog();
  renderChatContext();
}

async function checkCoachOnline() {
  const box = $("chat-offline");
  try {
    const status = await readJson(await fetch("/api/chat/status"));
    box.hidden = status.available;
    if (status.available) return;
    const code = document.createElement("code");
    code.textContent = status.start_command;
    box.replaceChildren("The coach's model isn't running. Start it in a terminal:", code);
  } catch {
    box.hidden = true; // asking will show the problem if there is one
  }
}

// --- Dialogs and buttons ----------------------------------------------------

$("new-game").addEventListener("click", () => $("new-dialog").showModal());
$("new-dialog").addEventListener("close", () => {
  const you = $("new-dialog").returnValue;
  if (you) startGame({ you }).catch((error) => showError(error.message));
  $("new-dialog").returnValue = "";
});

$("swap").addEventListener("click", () => swapSides());
$("retry").addEventListener("click", () => botTurnIfNeeded());
$("review").addEventListener("click", () => enterReview({ cursor: state.cursor }));
$("play-here").addEventListener("click", () => playFromHere().catch((error) => showError(error.message)));
$("continue").addEventListener("click", () => continueGame().catch((error) => showError(error.message)));

$("tab-analysis").addEventListener("click", () => {
  state.tab = "analysis";
  render();
});
$("tab-chat").addEventListener("click", () => {
  state.tab = "chat";
  render();
  checkCoachOnline();
});
$("chat-clear").addEventListener("click", () => {
  clearChat();
  renderChatContext();
  $("chat-input").focus();
});
$("chat-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const question = $("chat-input").value;
  if (state.chatBusy || !question.trim()) return;
  $("chat-input").value = "";
  askCoach(question);
});
$("chat-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    $("chat-form").requestSubmit();
  }
});
for (const suggestion of SUGGESTIONS) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = suggestion;
  button.addEventListener("click", () => askCoach(suggestion));
  $("chat-suggestions").append(button);
}

function bindSetting(id, key, storageKey, onChange = render) {
  const box = $(id);
  try {
    const saved = localStorage.getItem(storageKey);
    if (saved !== null) state[key] = saved === "true";
  } catch { /* the app works without remembering this */ }
  box.checked = state[key];
  box.addEventListener("change", () => {
    state[key] = box.checked;
    try { localStorage.setItem(storageKey, String(box.checked)); } catch { /* optional */ }
    if (!state.info) return; // still loading, and the game will start with this setting
    onChange();
    if (state.auto) syncAnalysis();
  });
}
bindSetting("auto-analyse", "auto", "chess-coach-auto");
bindSetting("show-best", "showBest", "chess-coach-show-best");
bindSetting("both-sides", "bothSides", "chess-coach-both-sides", setBothSides);

$("nav-start").addEventListener("click", () => setCursor(0));
$("nav-prev").addEventListener("click", () => setCursor(state.cursor - 1));
$("nav-next").addEventListener("click", () => setCursor(state.cursor + 1));
$("nav-end").addEventListener("click", () => setCursor(state.moves.length));
document.addEventListener("keydown", (event) => {
  if (document.querySelector("dialog[open]") || event.target.matches("select, textarea, input")) return;
  if (event.key === "ArrowLeft") setCursor(state.cursor - 1);
  if (event.key === "ArrowRight") setCursor(state.cursor + 1);
});

$("setup").addEventListener("click", () => {
  $("setup-text").value = "";
  $("setup-error").textContent = "";
  $("setup-dialog").showModal();
});

$("setup-go").addEventListener("click", async () => {
  const text = $("setup-text").value.trim();
  if (!text) {
    $("setup-error").textContent = "Paste a game or position first.";
    return;
  }
  try {
    if (looksLikeFen(text)) {
      await startGame({ startFen: text });
    } else {
      const game = await post("/api/pgn", { pgn: text });
      await openForReview({ startFen: game.start_fen, moves: game.moves, human: [], you: "white", saveable: false });
    }
    $("setup-dialog").close();
  } catch (error) {
    $("setup-error").textContent = error.message;
  }
});

function looksLikeFen(text) {
  return /^[pnbrqkPNBRQK1-8]+(\/[pnbrqkPNBRQK1-8]+){7}(\s|$)/.test(text);
}

async function openForReview({ startFen, moves, human, you, saveable, id = null, createdAt = null }) {
  const token = ++requestToken;
  cancelAnalysis();
  const info = await post("/api/position", { start_fen: startFen, moves });
  if (token !== requestToken) return;
  clearChat();
  Object.assign(state, {
    id: id ?? newGameId(),
    createdAt: createdAt ?? new Date().toISOString(),
    startFen,
    you,
    saveable,
    rows: [],
  });
  applyLine(info, moves, human);
  enterReview({ cursor: 0 });
}

$("saved").addEventListener("click", async () => {
  const list = $("saved-list");
  list.replaceChildren();
  $("saved-dialog").showModal();
  try {
    const games = await readJson(await fetch("/api/games"));
    if (games.length === 0) showNothingSaved();
    for (const game of games) list.append(savedRow(game));
  } catch (error) {
    const item = document.createElement("li");
    item.className = "error";
    item.textContent = error.message;
    list.append(item);
  }
});

function showNothingSaved() {
  const item = document.createElement("li");
  item.textContent = "Nothing saved yet. Games save themselves as you play.";
  $("saved-list").replaceChildren(item);
}

function smallButton(text, onClick, className = "") {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = text;
  button.addEventListener("click", onClick);
  return button;
}

function savedRow(game) {
  const item = document.createElement("li");
  const date = game.created_at ? new Date(game.created_at).toLocaleString() : "Unknown date";
  const open = smallButton(
    `${date} · ${game.move_count} moves · ${game.result === "*" ? "unfinished" : game.result}`,
    () => openSaved(game.id),
    "saved-open",
  );
  const showRow = () => item.replaceChildren(open, actions);
  let actions;
  // The open game saves itself after every move, so deleting it would only bring it back.
  if (game.id === state.id) {
    actions = document.createElement("span");
    actions.className = "saved-note";
    actions.textContent = "Open now";
  } else {
    actions = smallButton("Delete", () => askToDelete(), "saved-delete");
    actions.setAttribute("aria-label", `Delete the game from ${date}`);
  }
  const askToDelete = () => {
    const question = document.createElement("span");
    question.className = "saved-question";
    question.textContent = "Delete this game? This can't be undone.";
    const keep = smallButton("Keep", () => {
      showRow();
      actions.focus();
    });
    const confirm = smallButton("Delete", () => deleteSaved(game.id, item, question), "danger");
    item.replaceChildren(question, confirm, keep);
    keep.focus();
  };
  showRow();
  return item;
}

async function deleteSaved(id, item, question) {
  try {
    await readJson(await fetch(`/api/games/${id}`, { method: "DELETE" }));
    item.remove();
    if ($("saved-list").children.length === 0) showNothingSaved();
  } catch (error) {
    question.textContent = `Couldn't delete it: ${error.message}`;
  }
}

async function openSaved(id) {
  try {
    const game = await readJson(await fetch(`/api/games/${id}`));
    $("saved-dialog").close();
    await openForReview({
      startFen: game.start_fen,
      moves: game.moves,
      human: game.human ?? [],
      you: game.you ?? "white",
      saveable: true,
      id: game.id,
      createdAt: game.created_at,
    });
  } catch (error) {
    showError(error.message);
  }
}

// --- Start ------------------------------------------------------------------

async function init() {
  const levels = await readJson(await fetch("/api/levels"));
  for (const level of levels) {
    const option = document.createElement("option");
    option.value = level.id;
    option.textContent = level.label;
    $("level").append(option);
  }
  try {
    const saved = localStorage.getItem("chess-coach-level");
    if (saved && levels.some((level) => level.id === saved)) $("level").value = saved;
  } catch { /* the app works without remembering this */ }
  $("level").addEventListener("change", () => {
    try { localStorage.setItem("chess-coach-level", $("level").value); } catch { /* optional */ }
  });
  await startGame({ you: "white" });
}

init().catch((error) => {
  $("status").textContent = `The app couldn't start: ${error.message}`;
  $("status").classList.add("error");
});
