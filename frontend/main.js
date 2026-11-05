const COLS = "ABCDEFGHJ";

const app = {
  rules: null,
  agents: [],
  game: null,
  frame: 0,
  busy: false,
  error: "",
  auto: false,
  loop: 0,
  hover: null,
  hint: null,
  showTerritory: true,
};

const boardCanvas = document.getElementById("board");
const boardCtx = boardCanvas.getContext("2d");
const blackSelect = document.getElementById("black-player");
const whiteSelect = document.getElementById("white-player");
const thinkSelect = document.getElementById("think-ms");
const moveList = document.getElementById("move-list");
const libraryList = document.getElementById("library-list");
const agentList = document.getElementById("agent-list");

document.getElementById("create-game").addEventListener("click", createGame);
document.getElementById("pass-btn").addEventListener("click", () => send({ action: "PASS" }));
document.getElementById("resign-btn").addEventListener("click", () => send({ action: "RESIGN" }));
document.getElementById("step-btn").addEventListener("click", () => stepOnce());
document.getElementById("auto-btn").addEventListener("click", toggleAuto);
document.getElementById("export-btn").addEventListener("click", exportSgf);
document.getElementById("analyze-btn").addEventListener("click", rerunAnalysis);
document.getElementById("back-now").addEventListener("click", () => {
  if (!app.game) return;
  app.frame = app.game.frames.length - 1;
  app.hint = null;
  render();
});
document.getElementById("frame-range").addEventListener("input", (event) => {
  app.frame = Number(event.target.value);
  app.hint = null;
  render();
});
document.getElementById("territory-toggle").addEventListener("change", (event) => {
  app.showTerritory = event.target.checked;
  draw();
});
document.getElementById("agent-protocol").addEventListener("change", syncAgentForm);
document.getElementById("fill-sample").addEventListener("click", () => {
  if (app.rules) document.getElementById("agent-command").value = app.rules.sample_stdio_command;
  document.getElementById("agent-protocol").value = "stdio";
  syncAgentForm();
});
document.getElementById("save-agent").addEventListener("click", saveAgent);
moveList.addEventListener("click", onMoveClick);
libraryList.addEventListener("click", onLibraryClick);
agentList.addEventListener("click", onAgentClick);
boardCanvas.addEventListener("mousemove", onHover);
boardCanvas.addEventListener("mouseleave", () => {
  app.hover = null;
  draw();
});
boardCanvas.addEventListener("click", onBoardClick);
window.addEventListener("resize", resizeBoard);
window.addEventListener("keydown", (event) => {
  if (event.target.matches("input, textarea, select")) return;
  if (event.key === "p" || event.key === "P") send({ action: "PASS" });
  if (event.key === "r" || event.key === "R") send({ action: "RESIGN" });
});

boot();

async function boot() {
  resizeBoard();
  syncAgentForm();
  try {
    app.rules = await api("/api/rules");
    document.getElementById("rules-line").textContent =
      `棋盘 ${app.rules.board_size}×${app.rules.board_size} · 黑先 · 中国数子 · 黑贴 3¾ 子（7½ 目） · 胜子数 = 贴后面积差 ÷ 2 · 提子不重复计入 · 连续两次虚手终局 · 单步硬超时 ${app.rules.decision_timeout_ms / 1000} 秒`;
    app.agents = await api("/api/agents");
    fillPlayers();
    renderAgents();
    await loadLibrary();
  } catch (error) {
    app.error = error.message;
  }
  render();
}

async function createGame() {
  app.auto = false;
  app.loop += 1;
  document.getElementById("auto-btn").classList.remove("on");
  document.getElementById("auto-btn").textContent = "自动对战";
  app.busy = true;
  app.error = "";
  render();
  try {
    const game = await api("/api/games", {
      method: "POST",
      body: JSON.stringify({
        black_id: blackSelect.value,
        white_id: whiteSelect.value,
      }),
    });
    adopt(game);
    await loadLibrary();
  } catch (error) {
    app.error = error.message;
  } finally {
    app.busy = false;
    render();
  }
  await continueIfNeeded();
}

async function send(body) {
  if (!canHumanPlay()) return;
  app.busy = true;
  app.error = "";
  render();
  try {
    const game = await api(`/api/games/${app.game.id}/move`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    adopt(game);
    if (game.status === "finished") await loadLibrary();
  } catch (error) {
    app.error = error.message;
  } finally {
    app.busy = false;
    render();
  }
  await continueIfNeeded();
}

async function stepOnce() {
  if (!app.game || app.game.status !== "playing" || app.busy) return;
  if (currentSeatId() === "human") return;
  app.busy = true;
  app.error = "";
  render();
  try {
    const game = await api(`/api/games/${app.game.id}/step`, {
      method: "POST",
      body: JSON.stringify({ think_ms: Number(thinkSelect.value) }),
    });
    adopt(game);
    if (game.status === "finished") await loadLibrary();
  } catch (error) {
    app.error = error.message;
  } finally {
    app.busy = false;
    render();
  }
}

async function continueIfNeeded() {
  const game = app.game;
  if (!game || game.status !== "playing" || currentSeatId() === "human") return;
  if (game.mode === "ai_vs_ai") {
    if (app.auto) await autoLoop();
    return;
  }
  await stepOnce();
}

async function autoLoop() {
  const token = ++app.loop;
  while (token === app.loop && app.auto && app.game && app.game.status === "playing") {
    if (currentSeatId() === "human") break;
    await stepOnce();
    if (token !== app.loop || !app.auto) break;
    await sleep(240);
  }
}

function toggleAuto() {
  app.auto = !app.auto;
  const button = document.getElementById("auto-btn");
  button.classList.toggle("on", app.auto);
  button.textContent = app.auto ? "停止自动" : "自动对战";
  if (app.auto) continueIfNeeded();
  else app.loop += 1;
}

async function rerunAnalysis() {
  if (!app.game || app.busy) return;
  app.busy = true;
  render();
  try {
    const analysis = await api(`/api/games/${app.game.id}/analyze`, { method: "POST" });
    app.game.analysis = analysis;
  } catch (error) {
    app.error = error.message;
  } finally {
    app.busy = false;
    render();
  }
}

async function exportSgf() {
  if (!app.game) return;
  const response = await fetch(`/api/games/${app.game.id}/sgf`);
  if (!response.ok) {
    app.error = "棋谱导出失败";
    render();
    return;
  }
  const text = await response.text();
  const blob = new Blob([text], { type: "application/x-go-sgf" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `xiaqi-${app.game.id}.sgf`;
  link.click();
  URL.revokeObjectURL(link.href);
}

async function saveAgent() {
  app.error = "";
  try {
    await api("/api/agents", {
      method: "POST",
      body: JSON.stringify({
        name: document.getElementById("agent-name").value,
        protocol: document.getElementById("agent-protocol").value,
        command: document.getElementById("agent-command").value,
        endpoint: document.getElementById("agent-endpoint").value,
      }),
    });
    document.getElementById("agent-name").value = "";
    app.agents = await api("/api/agents");
    fillPlayers();
    renderAgents();
  } catch (error) {
    app.error = error.message;
    render();
  }
}

async function loadLibrary() {
  const games = await api("/api/games");
  if (!games.length) {
    libraryList.innerHTML = `<li class="muted">还没有对局</li>`;
    return;
  }
  libraryList.innerHTML = games.map((game) => {
    const result = summarize(game);
    return `<li><button type="button" data-game="${game.id}">${escapeHtml(game.black.name)} 对 ${escapeHtml(game.white.name)} · ${escapeHtml(result)}</button></li>`;
  }).join("");
}

function fillPlayers() {
  const black = blackSelect.value;
  const white = whiteSelect.value;
  const html = app.agents.map((agent) => `<option value="${escapeHtml(agent.id)}">${escapeHtml(agent.name)}</option>`).join("");
  blackSelect.innerHTML = html;
  whiteSelect.innerHTML = html;
  blackSelect.value = app.agents.some((agent) => agent.id === black) ? black : "human";
  whiteSelect.value = app.agents.some((agent) => agent.id === white) ? white : "builtin:heuristic";
}

function renderAgents() {
  const custom = app.agents.filter((agent) => agent.protocol === "stdio" || agent.protocol === "http");
  if (!custom.length) {
    agentList.innerHTML = `<li class="muted">还没有外接 AI</li>`;
    return;
  }
  agentList.innerHTML = custom.map((agent) => `
    <li>
      <span>${escapeHtml(agent.name)} · ${agent.protocol === "http" ? "HTTP" : "命令"}</span>
      <button class="tiny ghost" type="button" data-remove="${agent.id}">移除</button>
    </li>
  `).join("");
}

function adopt(game) {
  app.game = game;
  app.frame = game.frames.length - 1;
  app.hint = null;
}

function render() {
  draw();
  const status = document.getElementById("status-line");
  const error = document.getElementById("error-line");
  error.textContent = app.error || "";
  status.textContent = statusText();
  document.getElementById("black-label").textContent = app.game ? app.game.black.name : "尚未开局";
  document.getElementById("white-label").textContent = app.game ? app.game.white.name : "尚未开局";
  document.querySelector(".black-side").classList.toggle("active", app.game && app.game.to_play === "black");
  document.querySelector(".white-side").classList.toggle("active", app.game && app.game.to_play === "white");
  renderResult();
  renderMoves();
  renderAnalysis();
  updateButtons();
  const range = document.getElementById("frame-range");
  const max = app.game ? app.game.frames.length - 1 : 0;
  range.max = String(max);
  range.value = String(Math.min(app.frame, max));
}

function statusText() {
  if (!app.game) return "开局后黑先。点交叉点落子，P 虚手，R 认输。";
  if (app.busy && app.game && app.game.status === "playing" && currentSeatId() !== "human") {
    const side = app.game.to_play === "black" ? "黑方" : "白方";
    return `${side}思考中`;
  }
  if (app.game.status === "finished") return "对局结束，可以导出棋谱或回看手数。";
  if (!atLatest()) return "正在看前面的局面。回到当前后才能落子。";
  const side = app.game.to_play === "black" ? "黑方" : "白方";
  if (currentSeatId() === "human") return `轮到${side}。`;
  return `轮到${side}。可以让它走一步，或打开自动对战。`;
}

function renderResult() {
  const card = document.getElementById("result-card");
  const result = app.game && app.game.result;
  if (!result) {
    card.hidden = true;
    card.innerHTML = "";
    return;
  }
  card.hidden = false;
  const side = result.winner === "black" ? "黑方" : "白方";
  const name = result.winner === "black" ? app.game.black.name : app.game.white.name;
  const reason = {
    two_passes: "连续两次虚手",
    resign: "认输",
    timeout: "决策超时判负",
    illegal: "非法落子判负",
    protocol: "协议错误判负",
  }[result.end_reason] || result.end_reason;
  let html = `<p class="result-title">${side} ${escapeHtml(name)} · ${escapeHtml(reason)}</p>`;
  if (result.end_reason === "two_passes" && result.score) {
    const score = result.score;
    html += `<p>黑方面积 ${score.black_area}　白方面积 ${score.white_area}　单官 ${score.dame}</p>`;
    html += `<p>贴后面积差 = ${score.black_area} − ${score.white_area} − 7.5 = ${fmt(score.area_diff_after_komi)} 目</p>`;
    html += `<p>胜子数 = ${fmt(score.area_diff_after_komi)} ÷ 2 = ${fmt(score.win_stones)} 子</p>`;
    html += `<p class="win-line">${side}胜 ${fmt(Math.abs(score.win_stones))} 子</p>`;
  } else {
    html += `<p>${escapeHtml(result.detail || "")}</p>`;
    if (result.score) {
      html += `<p class="muted">参考面积：黑 ${result.score.black_area}，白 ${result.score.white_area}。这块面积不改判本局胜负。</p>`;
    }
  }
  card.innerHTML = html;
}

function renderMoves() {
  if (!app.game || !app.game.moves.length) {
    moveList.innerHTML = `<li class="muted">还没有落子</li>`;
    return;
  }
  const notes = new Map((app.game.analysis?.move_notes || []).map((note) => [note.move_number, note]));
  moveList.innerHTML = app.game.moves.map((move) => {
    const note = notes.get(move.move_number);
    const severity = note ? note.severity : "normal";
    const text = `${move.move_number}. ${move.color === "black" ? "黑" : "白"} ${moveLabel(move)}`;
    const current = app.frame === move.move_number ? "current" : "";
    return `<li><button type="button" class="${severity} ${current}" data-move="${move.move_number}">${escapeHtml(text)}</button></li>`;
  }).join("");
}

function renderAnalysis() {
  const box = document.getElementById("analysis-box");
  const analysis = app.game && app.game.analysis;
  if (!analysis) {
    box.innerHTML = `<p class="muted">终局后给出缺点、参考着和胜子数。</p>`;
    return;
  }
  const cards = (analysis.weaknesses || []).map((item) => `
    <article class="weak ${item.player}">
      <h3>${escapeHtml(item.title)}</h3>
      <p>${escapeHtml(item.detail)}</p>
    </article>
  `).join("");
  box.innerHTML = `<p>${escapeHtml(analysis.summary || "")}</p><p class="advice">${escapeHtml(analysis.advice || "")}</p>${cards}`;
}

function updateButtons() {
  const human = canHumanPlay();
  document.getElementById("pass-btn").disabled = !human;
  document.getElementById("resign-btn").disabled = !human;
  const aiTurn = app.game && app.game.status === "playing" && currentSeatId() !== "human";
  document.getElementById("step-btn").disabled = app.busy || !aiTurn || app.auto;
  document.getElementById("create-game").disabled = app.busy;
  document.getElementById("export-btn").disabled = !app.game;
  document.getElementById("analyze-btn").disabled = !app.game || app.busy;
}

function onMoveClick(event) {
  const button = event.target.closest("[data-move]");
  if (!button || !app.game) return;
  const number = Number(button.dataset.move);
  const note = (app.game.analysis?.move_notes || []).find((item) => item.move_number === number);
  if (note && note.suggestion) {
    app.frame = Math.max(0, number - 1);
    app.hint = parseVertex(note.suggestion);
  } else {
    app.frame = number;
    app.hint = null;
  }
  render();
}

async function onLibraryClick(event) {
  const button = event.target.closest("[data-game]");
  if (!button) return;
  app.auto = false;
  app.loop += 1;
  try {
    adopt(await api(`/api/games/${button.dataset.game}`));
    app.error = "";
  } catch (error) {
    app.error = error.message;
  }
  render();
}

async function onAgentClick(event) {
  const button = event.target.closest("[data-remove]");
  if (!button) return;
  try {
    await api(`/api/agents/${button.dataset.remove}`, { method: "DELETE" });
    app.agents = await api("/api/agents");
    fillPlayers();
    renderAgents();
  } catch (error) {
    app.error = error.message;
    render();
  }
}

function onHover(event) {
  const point = eventToPoint(event);
  app.hover = point && isLegal(point) ? point : null;
  draw();
}

function onBoardClick(event) {
  if (!canHumanPlay()) return;
  const point = eventToPoint(event);
  if (!point || !isLegal(point)) {
    app.error = point ? "这一着被规则挡住了" : "";
    render();
    return;
  }
  send({ action: "PLACE", x: point.x, y: point.y });
}

function resizeBoard() {
  const shell = document.querySelector(".board-shell");
  const size = Math.max(280, Math.min(shell.clientWidth - 28, 680));
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  boardCanvas.style.width = `${size}px`;
  boardCanvas.style.height = `${size}px`;
  boardCanvas.width = Math.round(size * dpr);
  boardCanvas.height = Math.round(size * dpr);
  boardCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
  boardCanvas._size = size;
  draw();
}

function draw() {
  const size = boardCanvas._size || boardCanvas.clientWidth || 640;
  const ctx = boardCtx;
  const pad = 34;
  const grid = (size - pad * 2) / 8;
  ctx.clearRect(0, 0, size, size);
  const wood = ctx.createLinearGradient(0, 0, size, size);
  wood.addColorStop(0, "#e8c27d");
  wood.addColorStop(0.48, "#d7a45a");
  wood.addColorStop(1, "#c4893c");
  ctx.fillStyle = wood;
  ctx.fillRect(0, 0, size, size);
  ctx.strokeStyle = "rgba(90, 48, 18, 0.35)";
  ctx.strokeRect(8, 8, size - 16, size - 16);
  ctx.beginPath();
  ctx.strokeStyle = "#4a2a12";
  ctx.lineWidth = 1.2;
  for (let i = 0; i < 9; i += 1) {
    ctx.moveTo(pad, pad + i * grid);
    ctx.lineTo(size - pad, pad + i * grid);
    ctx.moveTo(pad + i * grid, pad);
    ctx.lineTo(pad + i * grid, size - pad);
  }
  ctx.stroke();
  for (const [x, y] of [[2, 2], [2, 6], [6, 2], [6, 6], [4, 4]]) {
    const at = pointPx(x, y, pad, grid);
    ctx.beginPath();
    ctx.fillStyle = "#3d2412";
    ctx.arc(at.px, at.py, 3.3, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.fillStyle = "#5a3b22";
  ctx.font = "12px Segoe UI, Microsoft YaHei, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  for (let x = 0; x < 9; x += 1) {
    ctx.fillText(COLS[x], pad + x * grid, 16);
    ctx.fillText(COLS[x], pad + x * grid, size - 16);
  }
  for (let y = 0; y < 9; y += 1) {
    const py = pad + (8 - y) * grid;
    ctx.textAlign = "right";
    ctx.fillText(String(y + 1), pad - 10, py);
    ctx.textAlign = "left";
    ctx.fillText(String(y + 1), size - pad + 10, py);
  }
  const board = currentBoard();
  const radius = grid * 0.46;
  if (app.showTerritory && showTerritoryNow()) {
    const owner = app.game.result.score.owner;
    for (let y = 0; y < 9; y += 1) {
      for (let x = 0; x < 9; x += 1) {
        const mark = owner[y][x];
        if (mark !== 1 && mark !== 2) continue;
        const at = pointPx(x, y, pad, grid);
        ctx.fillStyle = mark === 1 ? "rgba(20, 16, 12, 0.28)" : "rgba(255, 250, 240, 0.55)";
        ctx.fillRect(at.px - grid * 0.16, at.py - grid * 0.16, grid * 0.32, grid * 0.32);
      }
    }
  }
  for (let y = 0; y < 9; y += 1) {
    for (let x = 0; x < 9; x += 1) {
      if (!board[y][x]) continue;
      const at = pointPx(x, y, pad, grid);
      drawStone(ctx, at.px, at.py, radius, board[y][x] === 1 ? "black" : "white");
    }
  }
  const frame = app.game && app.game.frames[app.frame];
  if (frame && frame.last_move && frame.last_move.x !== null && frame.last_move.x !== undefined) {
    const at = pointPx(frame.last_move.x, frame.last_move.y, pad, grid);
    ctx.fillStyle = frame.last_move.color === "black" ? "#f2d48a" : "#c44536";
    ctx.fillRect(at.px - 4, at.py - 4, 8, 8);
  }
  if (app.hint && board[app.hint.y][app.hint.x] === 0) {
    const at = pointPx(app.hint.x, app.hint.y, pad, grid);
    ctx.strokeStyle = "#8a5a12";
    ctx.lineWidth = 2;
    ctx.strokeRect(at.px - radius, at.py - radius, radius * 2, radius * 2);
  }
  if (app.hover && canHumanPlay()) {
    const at = pointPx(app.hover.x, app.hover.y, pad, grid);
    ctx.globalAlpha = 0.45;
    drawStone(ctx, at.px, at.py, radius, app.game.to_play === "black" ? "black" : "white");
    ctx.globalAlpha = 1;
  }
}

function drawStone(ctx, x, y, radius, color) {
  ctx.beginPath();
  ctx.fillStyle = "rgba(48, 28, 12, 0.25)";
  ctx.ellipse(x + 1.4, y + radius * 0.16, radius * 0.82, radius * 0.26, 0, 0, Math.PI * 2);
  ctx.fill();
  const gradient = ctx.createRadialGradient(x - radius * 0.35, y - radius * 0.42, radius * 0.1, x, y, radius);
  if (color === "black") {
    gradient.addColorStop(0, "#6e6a65");
    gradient.addColorStop(0.42, "#2b2724");
    gradient.addColorStop(1, "#0d0c0b");
  } else {
    gradient.addColorStop(0, "#ffffff");
    gradient.addColorStop(0.55, "#f4f0e8");
    gradient.addColorStop(1, "#d1c7b8");
  }
  ctx.beginPath();
  ctx.fillStyle = gradient;
  ctx.arc(x, y, radius, 0, Math.PI * 2);
  ctx.fill();
}

function pointPx(x, y, pad, grid) {
  return { px: pad + x * grid, py: pad + (8 - y) * grid };
}

function eventToPoint(event) {
  const size = boardCanvas._size || boardCanvas.clientWidth;
  const rect = boardCanvas.getBoundingClientRect();
  const mx = (event.clientX - rect.left) * (size / rect.width);
  const my = (event.clientY - rect.top) * (size / rect.height);
  const pad = 34;
  const grid = (size - pad * 2) / 8;
  const x = Math.round((mx - pad) / grid);
  const y = 8 - Math.round((my - pad) / grid);
  if (x < 0 || y < 0 || x > 8 || y > 8) return null;
  const at = pointPx(x, y, pad, grid);
  if (Math.hypot(at.px - mx, at.py - my) > grid * 0.48) return null;
  return { x, y };
}

function currentBoard() {
  if (!app.game) return Array.from({ length: 9 }, () => Array(9).fill(0));
  const frame = app.game.frames[app.frame] || app.game.frames[app.game.frames.length - 1];
  return frame.board;
}

function showTerritoryNow() {
  return app.game && app.game.result && app.game.result.score && app.frame === app.game.frames.length - 1;
}

function isLegal(point) {
  if (!app.game || !atLatest()) return false;
  return app.game.legal_moves.some((move) => move.x === point.x && move.y === point.y);
}

function canHumanPlay() {
  return Boolean(app.game && app.game.status === "playing" && !app.busy && atLatest() && currentSeatId() === "human");
}

function atLatest() {
  return Boolean(app.game && app.frame === app.game.frames.length - 1);
}

function currentSeatId() {
  if (!app.game || app.game.status !== "playing") return null;
  return app.game.to_play === "black" ? app.game.black.id : app.game.white.id;
}

function moveLabel(move) {
  if (move.action === "PLACE") return move.vertex || "";
  if (move.action === "PASS") return "虚手";
  if (move.action === "RESIGN") return "认输";
  if (move.action === "TIMEOUT") return "超时判负";
  if (move.action === "ILLEGAL") return "非法落子判负";
  if (move.action === "PROTOCOL") return "协议错误判负";
  return move.action;
}

function summarize(game) {
  if (game.status !== "finished") return "进行中";
  const side = game.winner === "black" ? "黑胜" : game.winner === "white" ? "白胜" : "结束";
  if (game.end_reason === "two_passes" && game.win_stones !== null && game.win_stones !== undefined) {
    return `${side} ${fmt(Math.abs(game.win_stones))} 子`;
  }
  const reason = { resign: "认输", timeout: "超时", illegal: "非法", protocol: "协议" }[game.end_reason] || "";
  return `${side} ${reason}`.trim();
}

function parseVertex(vertex) {
  const col = COLS.indexOf(vertex[0]);
  const row = Number(vertex.slice(1));
  if (col < 0 || !Number.isFinite(row)) return null;
  return { x: col, y: row - 1 };
}

function syncAgentForm() {
  const http = document.getElementById("agent-protocol").value === "http";
  document.getElementById("command-label").hidden = http;
  document.getElementById("endpoint-label").hidden = !http;
}

async function api(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (response.status === 204) return null;
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const detail = data && data.detail ? data.detail : response.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

function fmt(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return String(Math.round(number * 100) / 100);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
