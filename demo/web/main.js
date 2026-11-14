"use strict";
const $ = (id) => document.getElementById(id);
const COLS = "ABCDEFGHJ";
const state = {data:null, mode:"challenge", color:"black", game:null, frame:0, busy:false, auto:false, timer:null, generation:0, error:"", hover:null, focus:{x:4,y:4}, elapsed:null, history:[]};
const canvas = $("board");
const ctx = canvas.getContext("2d");
let geometry = {size:550, pad:34, grid:60};

async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "请求未完成，请重试。");
  return data;
}
function safe(value) { return String(value).replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
function icon(name) { return `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`; }
function nameFor(color) {return color === "black" ? "黑棋" : "白棋";}
function stop() { state.auto = false; state.generation += 1; clearTimeout(state.timer); state.timer = null; }
function latest() { return Boolean(state.game && state.frame === state.game.frames.length-1); }
function currentPlayer() { return state.game?.[state.game.to_play]?.id; }
function humanCanPlay() {return Boolean(state.game && !state.game.readonly && state.game.status === "playing" && currentPlayer() === "human" && !state.busy && latest());}
function adopt(game, frame) {state.game=game; state.frame=frame ?? game.frames.length-1;state.hover=null;state.error="";}
function ready() {return state.data?.models.every((m) => m.available);}
function setError(error) {state.error=error.message || String(error); stop(); render();}

async function boot() {
  resize();
  try {
    state.data = await api("/api/demo");
    const d=state.data;
    $("stat-rate").textContent=(d.primary.score_rate*100).toFixed(1);
    $("stat-record").textContent=`${d.primary.wins} 胜 / ${d.primary.games} 局`;
    $("stat-games").textContent=d.training_games;
    $("stat-positions").textContent=d.new_positions.toLocaleString("en-US");
    $("stat-time").textContent=d.native.max_decision_seconds.toFixed(2);
    renderEvidence(); fillReplays(); renderReadiness(); await loadHistory();
    const saved=localStorage.getItem("xiaqi-demo-last-game");
    if (saved) {
      try {adopt(await api(`/api/games/${encodeURIComponent(saved)}`));}
      catch {localStorage.removeItem("xiaqi-demo-last-game");}
    }
    if (state.game && !state.game.readonly) {
      state.mode=state.game.mode === "ai_vs_ai" ? "battle" : "challenge";
      state.color=state.game.black.id === "human" ? "black" : "white";
      syncMode();
    }
    render();
    // A restored AI turn resumes as one move; an old automated battle stays paused.
    if (state.game?.mode === "human_vs_ai" && state.game.status === "playing" && currentPlayer() !== "human") await step();
  } catch (error) {
    $("load-error").hidden=false;
    $("load-error").textContent=`无法连接演示服务：${error.message}。请确认启动窗口仍在运行，然后刷新页面。`;
  }
  render();
}

function renderReadiness() {
  const available=ready();
  const node=$("engine-status"); node.classList.toggle("failed",!available);
  node.innerHTML=`<span class="status-dot"></span><span>${available ? "强化版权重已核验 · 本地就绪" : safe(state.data?.models.find((m) => !m.available)?.detail || "模型尚未就绪")}</span>`;
}
function syncMode() {
  document.querySelectorAll("[data-mode]").forEach((b) => {const active=b.dataset.mode===state.mode;b.classList.toggle("selected",active);b.setAttribute("aria-pressed",active);});
  const config={challenge:["HUMAN × AI","01","来挑战强化版。","你来落子，AI 来接招。<br>无需设置，选好执子就能开始。"],battle:["AI × AI","02","让进步，自己说话。","强化版与原始版正面对弈。<br>同一程序，两份权重，实时走棋。"],replay:["REPLAY × 200","03","回到真实的评测现场。","全部 200 局，无论胜负。<br>自由选择，逐手回看。"]}[state.mode];
  [$("mode-kicker").textContent,$("mode-number").textContent,$("mode-title").textContent]=config;
  $("mode-description").innerHTML=config[3];
  $("color-options").hidden=state.mode!=="challenge";
  $("battle-options").hidden=state.mode!=="battle";
  $("replay-options").hidden=state.mode!=="replay";
  document.querySelectorAll("[data-color]").forEach((b) => {const active=b.dataset.color===state.color;b.classList.toggle("selected",active);b.setAttribute("aria-pressed",active);});
  render();
}
async function switchMode(mode) {
  if (state.busy) return;
  stop(); state.mode=mode; state.error=""; syncMode();
  if (mode === "replay" && state.data && (!state.game?.readonly || state.game.replay_id!==Number($("replay-select").value))) await loadReplay(Number($("replay-select").value));
}
function page(name) {
  $("play-page").hidden=name!=="play";$("research-page").hidden=name!=="research";
  document.querySelectorAll(".nav-item").forEach((b) => {const active=b.dataset.page===name;b.classList.toggle("active",active);b.setAttribute("aria-pressed",active);});
  if (name === "research") stop();
  requestAnimationFrame(resize); render();
}

async function start() {
  if (state.busy) return;
  stop(); state.elapsed=null;state.error="";
  if (state.mode === "replay") {
    if (!state.game?.readonly || state.game.replay_id!==Number($("replay-select").value)) await loadReplay(Number($("replay-select").value));
    if (state.error) return;
    if (latest()) state.frame=0;
    state.auto=true; schedule();render();return;
  }
  state.busy=true;render();
  let black,white;
  if (state.mode === "challenge") {black=state.color === "black" ? "human" : "demo:trained";white=state.color === "white" ? "human" : "demo:trained";}
  else {black=$("battle-colors").value === "trained" ? "demo:trained" : "demo:baseline";white=black === "demo:trained" ? "demo:baseline" : "demo:trained";}
  try {
    adopt(await api("/api/games",{black_id:black,white_id:white}));
    localStorage.setItem("xiaqi-demo-last-game",state.game.id);
    await loadHistory();
  } catch(error){setError(error);}
  finally{state.busy=false;render();}
  if (state.error) return;
  if (state.mode === "battle") {state.auto=true;schedule();render();}
  else if (currentPlayer() !== "human") await step();
}

async function humanMove(body) {
  if (!humanCanPlay()) return;
  state.busy=true;state.error="";render();
  try {adopt(await api(`/api/games/${state.game.id}/move`,body));if(state.game.status === "finished") await loadHistory();}
  catch(error){setError(error);}
  finally{state.busy=false;render();}
  if (!state.error && state.game.status === "playing" && currentPlayer() !== "human") await step();
}
async function step() {
  if (state.busy || !state.game) return;
  if (state.game.readonly) {if(!latest()){state.frame+=1;render();}return;}
  if (state.game.status!=="playing" || currentPlayer()==="human") return;
  const id=state.game.id;
  state.busy=true;state.error="";render();const startTime=performance.now();
  try {
    const game=await api(`/api/games/${id}/step`,{think_ms:2500});
    if (state.game?.id!==id) return;
    adopt(game);state.elapsed=(performance.now()-startTime)/1000;
    if(game.status === "finished") {stop();await loadHistory();}
  } catch(error){setError(error);}
  finally{state.busy=false;render();}
}
function schedule() {
  clearTimeout(state.timer);const generation=state.generation;
  if (!state.auto || !state.game) return;
  const delay=state.game.readonly ? Number($("replay-speed").value) : 180;
  state.timer=setTimeout(async() => {
    if (!state.auto || generation!==state.generation) return;
    if (state.busy) {schedule();return;}
    await step();
    if (generation!==state.generation) return;
    if ((state.game.readonly && latest()) || (!state.game.readonly && state.game.status!=="playing")) {stop();render();return;}
    schedule();
  },delay);
}
function toggleAuto() {
  if (state.auto) stop();
  else {state.auto=true;if(state.game?.readonly && latest())state.frame=0;schedule();}
  render();
}
function seek(frame) {if(!state.game)return;stop();state.frame=Math.max(0,Math.min(frame,state.game.frames.length-1));state.hover=null;render();}

function fillReplays() {
  if(!state.data)return;
  const filter=$("replay-filter").value;
  const records=state.data.replays.filter((r) => filter === "all" || (filter === "win" ? r.result>0 : r.result<0));
  $("replay-select").innerHTML=records.map((r) => `<option value="${r.id}">第 ${String(r.id+1).padStart(3,"0")} 局 · 强化版${r.candidate_black ? "执黑" : "执白"} · ${r.result>0 ? "胜" : "负"}</option>`).join("");
}
async function loadReplay(index) {
  if (state.busy || !Number.isInteger(index)) return;
  stop();state.busy=true;state.error="";state.elapsed=null;render();
  try {adopt(await api(`/api/demo/replays/${index}`),0);}
  catch(error){setError(error);}
  finally{state.busy=false;render();}
}
async function loadHistory() {
  state.history=(await api("/api/games")).filter((g) => g.black.id.startsWith("demo:") || g.white.id.startsWith("demo:"));
  $("history").innerHTML=state.history.length ? state.history.slice(0,6).map((g) => `<button data-history="${safe(g.id)}">${safe(g.black.name)} × ${safe(g.white.name)}<br><span>${g.status === "finished" ? `${nameFor(g.winner)}胜` : "进行中"} · ${safe(g.id.slice(0,6))}</span></button>`).join("") : '<p class="empty-note">开始一盘，留下你的棋谱。</p>';
}
async function openHistory(id) {
  if(state.busy)return;stop();state.busy=true;render();
  try {adopt(await api(`/api/games/${encodeURIComponent(id)}`));state.mode=state.game.mode === "ai_vs_ai" ? "battle" : "challenge";state.color=state.game.black.id==="human" ? "black" : "white";localStorage.setItem("xiaqi-demo-last-game",id);syncMode();}
  catch(error){setError(error);}
  finally{state.busy=false;render();}
  if(state.game?.mode === "human_vs_ai" && state.game.status === "playing" && currentPlayer()!=="human") await step();
}

function render() {
  const g=state.game;const replay=Boolean(g?.readonly);const playing=g?.status === "playing";
  const selectable=Boolean(state.data && (state.mode === "replay" || ready()));
  $("start").disabled=state.busy || !selectable;
  $("start-label").textContent=state.busy ? "请稍候…" : state.mode === "challenge" ? "开始对弈" : state.mode === "battle" ? "开始 AI 对战" : "播放这盘棋";
  $("replace-note").hidden=!g || replay || !playing || state.mode === "replay";
  document.querySelectorAll("[data-mode],[data-color],#battle-colors,#replay-filter,#replay-select").forEach((el) => el.disabled=state.busy);
  let black=state.mode === "challenge" && state.color === "black" ? "你" : "夏棋 · 强化版";
  let white=state.mode === "challenge" && state.color === "white" ? "你" : state.mode === "battle" ? "夏棋 · 原始版" : "夏棋 · 强化版";
  if(g){black=g.black.name === "人类" ? "你" : g.black.name;white=g.white.name === "人类" ? "你" : g.white.name;}
  $("black-name").textContent=black;$("white-name").textContent=white;
  const viewing=g?.frames[state.frame];
  ["black","white"].forEach((color) => $(`${color}-player`).classList.toggle("active",Boolean(g && (replay || !latest() ? viewing.to_play === color : playing && g.to_play === color))));
  $("board-title").textContent=replay ? "独立评测回放" : "九路棋室";
  $("board-context").textContent=replay ? `第 ${g.replay_id+1} / 200 局 · 真实评测棋谱` : "真实模型 · 本地运行";
  $("board-dot").style.background=replay ? "#b59e75" : "";
  $("position-label").textContent=`第 ${state.frame} 手`;
  $("timeline").max=g ? g.frames.length-1 : 0;$("timeline").value=state.frame;$("timeline").disabled=!g;
  $("latest").disabled=!g || latest();$("latest").textContent=replay ? "查看终局 ↗" : "回到当前 ↗";
  $("pass").hidden=replay || g?.mode === "ai_vs_ai";$("resign").hidden=$("pass").hidden;
  $("pass").disabled=!humanCanPlay();$("resign").disabled=!humanCanPlay();
  const watch=Boolean(g && (replay || g.mode === "ai_vs_ai"));
  $("autoplay").hidden=!watch;$("step").hidden=!watch;
  $("autoplay").disabled=!watch || (!replay && !playing);
  $("step").disabled=!watch || state.busy || state.auto || (replay ? latest() : !playing);
  $("autoplay").innerHTML=icon(state.auto ? "pause" : "play")+`<span>${state.auto ? "暂停" : replay ? "播放回放" : "继续对战"}</span>`;
  $("thinking").hidden=!state.busy || !g || replay || currentPlayer()==="human";
  $("game-error").hidden=!state.error;$("game-error").textContent=state.error;
  $("export").disabled=!g;
  let status="准备好你的第一手。",detail="点击「开始对弈」，体验强化学习后的真实模型。",badge="等待开局";
  if(g) {
    if(replay){status=latest() ? "这一盘，已有答案。" : `回看第 ${state.frame} 手`;detail=`${state.auto ? "正在播放" : "回放已暂停"} · 共 ${g.move_count} 手，可拖动棋盘下方进度条。`;badge="真实评测";}
    else if(!latest()){status=`回看第 ${state.frame} 手`;detail="回到当前局面，即可继续对弈。";badge="回看中";}
    else if(!playing){status="一盘结束，进步继续。";detail="可以回看每一手，也可以再开一局。";badge="已结束";}
    else if(state.busy){status=`${nameFor(g.to_play)}正在思考…`;detail="神经网络评估局面，搜索下一手落点。";badge="思考中";}
    else if(currentPlayer()==="human"){status=`轮到你了，${nameFor(g.to_play)}。`;detail="点击棋盘交叉点落子，也可以选择虚手。";badge="你的回合";}
    else{status=state.auto ? "两份模型，正在交锋。" : "棋局已暂停。";detail=state.auto ? "可以随时暂停，逐手观察 AI 的选择。" : "点击继续对战，或让 AI 走下一手。";badge=state.auto ? "对战中" : "已暂停";}
    if(state.elapsed!==null && !state.busy)detail+=` 上手响应 ${state.elapsed.toFixed(2)} 秒。`;
  }
  $("game-status").textContent=status;$("session-detail").textContent=detail;$("session-badge").textContent=badge;
  renderResult();renderMoves();draw();
}
function renderResult() {
  const g=state.game,result=g?.result;
  $("result").hidden=!result || (g.readonly && !latest());
  if(!result)return;
  const winner=result.winner ? nameFor(result.winner) : "双方";
  const score=result.score;
  if(result.end_reason === "two_passes" && score) {
    $("result").innerHTML=`<strong>${winner}胜 ${Math.abs(score.win_stones).toFixed(2).replace(/0$/,'')} 子</strong><p>黑面积 ${score.black_area} · 白面积 ${score.white_area}<br>贴后面积差 ${score.area_diff_after_komi} 目 ÷ 2 = ${score.win_stones} 子<br>连续两次虚手，中国数子结算。</p>`;
  } else {
    const reason={resign:"对手认输",timeout:"对手超时",illegal:"对手非法落子",protocol:"对手响应错误"}[result.end_reason] || "对局结束";
    $("result").innerHTML=`<strong>${winner}胜</strong><p>${reason}${result.detail ? ` · ${safe(result.detail)}` : ""}</p>`;
  }
}
function renderMoves() {
  const moves=state.game?.moves.slice(0,state.frame) || [];
  $("moves").innerHTML=moves.length ? moves.slice(-8).map((m) => `<button data-frame="${m.move_number}" class="${m.move_number===state.frame ? "selected" : ""}"><span class="stone ${m.color}"></span>${m.move_number} · ${safe(m.vertex || ({PASS:"虚手",RESIGN:"认输"}[m.action] || m.action))}</button>`).join("") : '<p class="empty-note">每一手，都将留下记录。</p>';
}

function resize() {
  const width=canvas.parentElement.clientWidth-22;
  if(width<50)return;
  const size=Math.min(620,width);const dpr=Math.min(window.devicePixelRatio || 1,2);
  canvas.width=Math.round(size*dpr);canvas.height=Math.round(size*dpr);canvas.style.width="100%";canvas.style.height="auto";
  ctx.setTransform(dpr,0,0,dpr,0,0);
  const pad=size<380 ? 25 : 33;geometry={size,pad,grid:(size-pad*2)/8};draw();
}
function point(x,y){return {x:geometry.pad+x*geometry.grid,y:geometry.pad+(8-y)*geometry.grid};}
function drawStone(x,y,r,color,alpha=1) {
  ctx.save();ctx.globalAlpha=alpha;ctx.shadowColor="rgba(68,47,18,.21)";ctx.shadowBlur=3;ctx.shadowOffsetY=2;
  const grad=ctx.createRadialGradient(x-r*.3,y-r*.4,r*.05,x,y,r);
  if(color===1){grad.addColorStop(0,"#5a6058");grad.addColorStop(.43,"#303630");grad.addColorStop(1,"#1c211d");}
  else{grad.addColorStop(0,"#fffffd");grad.addColorStop(.55,"#f6f4eb");grad.addColorStop(1,"#dfded3");}
  ctx.beginPath();ctx.arc(x,y,r,0,Math.PI*2);ctx.fillStyle=grad;ctx.fill();ctx.restore();
}
function draw() {
  const {size,pad,grid}=geometry;
  ctx.clearRect(0,0,size,size);
  const wood=ctx.createLinearGradient(0,0,size,size);wood.addColorStop(0,"#e9d0a2");wood.addColorStop(.52,"#e1c18c");wood.addColorStop(1,"#d9b580");ctx.fillStyle=wood;ctx.fillRect(0,0,size,size);
  // Draw subtle grain deterministically, with no external images or network fonts.
  ctx.strokeStyle="rgba(115,77,28,.035)";ctx.lineWidth=.6;
  for(let i=0;i<95;i++){const x=(i*79.3)%size;ctx.beginPath();ctx.moveTo(x,0);ctx.bezierCurveTo(x+8,size*.3,x-5,size*.7,x+4,size);ctx.stroke();}
  ctx.strokeStyle="#8c754c";ctx.lineWidth=.75;ctx.beginPath();
  for(let i=0;i<9;i++){const at=pad+i*grid;ctx.moveTo(pad,at);ctx.lineTo(size-pad,at);ctx.moveTo(at,pad);ctx.lineTo(at,size-pad);}ctx.stroke();
  ctx.strokeStyle="#7b6745";ctx.lineWidth=1.2;ctx.strokeRect(pad,pad,size-pad*2,size-pad*2);
  for(const [x,y] of [[2,2],[2,6],[6,2],[6,6],[4,4]]){const p=point(x,y);ctx.beginPath();ctx.arc(p.x,p.y,size/180,0,Math.PI*2);ctx.fillStyle="#7c6743";ctx.fill();}
  ctx.font=`${size<380 ? 9 : 10}px Segoe UI,sans-serif`;ctx.fillStyle="#907a55";ctx.textAlign="center";ctx.textBaseline="middle";
  for(let i=0;i<9;i++){ctx.fillText(COLS[i],pad+i*grid,12);ctx.fillText(COLS[i],pad+i*grid,size-11);ctx.fillText(String(i+1),11,pad+(8-i)*grid);ctx.fillText(String(i+1),size-11,pad+(8-i)*grid);}
  const frame=state.game?.frames[state.frame];
  const board=frame?.board || Array.from({length:9},()=>Array(9).fill(0));
  const showScore=Boolean(state.game?.result?.score && latest());
  if(showScore){const owner=state.game.result.score.owner;for(let y=0;y<9;y++)for(let x=0;x<9;x++){if(board[y][x] || !owner[y][x])continue;const p=point(x,y);ctx.fillStyle=owner[y][x]===1 ? "#52613d77" : "#fffdf2bb";ctx.fillRect(p.x-grid*.1,p.y-grid*.1,grid*.2,grid*.2);}}
  for(let y=0;y<9;y++)for(let x=0;x<9;x++)if(board[y][x]){const p=point(x,y);drawStone(p.x,p.y,grid*.445,board[y][x]);}
  if(frame?.last_move){const move=frame.last_move;const p=point(move.x,move.y);ctx.beginPath();ctx.arc(p.x,p.y,grid*.11,0,Math.PI*2);ctx.strokeStyle=move.color==="black" ? "#dce8bd" : "#9a6a4a";ctx.lineWidth=1.6;ctx.stroke();}
  if(state.hover && humanCanPlay() && legal(state.hover)){const p=point(state.hover.x,state.hover.y);drawStone(p.x,p.y,grid*.445,state.game.to_play==="black" ? 1 : 2,.48);}
  if(document.activeElement===canvas && humanCanPlay()){const p=point(state.focus.x,state.focus.y);ctx.strokeStyle="#537144";ctx.lineWidth=2;ctx.strokeRect(p.x-grid*.36,p.y-grid*.36,grid*.72,grid*.72);}
}
function eventPoint(event) {
  const rect=canvas.getBoundingClientRect(),scale=geometry.size/rect.width;
  const x=Math.round(((event.clientX-rect.left)*scale-geometry.pad)/geometry.grid);
  const y=8-Math.round(((event.clientY-rect.top)*scale-geometry.pad)/geometry.grid);
  if(x<0||x>8||y<0||y>8)return null;
  const p=point(x,y);if(Math.hypot(p.x-(event.clientX-rect.left)*scale,p.y-(event.clientY-rect.top)*scale)>geometry.grid*.48)return null;
  return{x,y};
}
function legal(p){return state.game?.legal_moves.some((m)=>m.x===p.x&&m.y===p.y);}

function renderEvidence() {
  const d=state.data,p=d.primary;
  $("research-rate").textContent=(p.score_rate*100).toFixed(1);
  $("wins-bar").style.width=`${p.score_rate*100}%`;$("losses-bar").style.width=`${100-p.score_rate*100}%`;
  $("win-count").textContent=p.wins;$("loss-count").textContent=p.games-p.wins-p.draws;
  $("paired-result").textContent=`100 组配对开局：${p.pairs_won} 组占优 · ${p.pairs_lost} 组落后 · ${p.pairs_tied} 组持平。配对符号检验 p = ${p.pair_sign_p.toExponential(2)}。`;
  $("round-chart").innerHTML=d.training_rounds.map((r)=>`<div style="height:${r.score_rate*170}px" title="第 ${r.round} 轮：${r.wins}/${r.evaluated_games}，新增 ${r.positions} 个局面"><strong>${(r.score_rate*100).toFixed(1)}%</strong><span>第 ${r.round} 轮</span></div>`).join("");
  $("game-grid").innerHTML=d.replays.map((r)=>`<button class="${r.result>0 ? "win" : "loss"}" data-replay="${r.id}" title="第 ${r.id+1} 局：强化版${r.candidate_black ? "执黑" : "执白"}，${r.result>0 ? "胜" : "负"}，共 ${r.moves} 手" aria-label="回放第 ${r.id+1} 局，强化版${r.result>0 ? "胜" : "负"}"></button>`).join("");
  $("gnugo-comparison").innerHTML=[['原始版',d.gnugo.baseline_wins],['强化版',d.gnugo.trained_wins]].map(([label,wins])=>`<div><span>${label}</span><span><i style="width:${wins/d.gnugo.games*100}%"></i></span><b>${wins}/16</b></div>`).join("");
  $("native-score").textContent=`强化版 ${d.native.wins} 胜 / ${d.native.games} 局`;
  $("native-detail").textContent=`${d.native.decisions} 次决策均合法。每手默认 32 次搜索，最慢 ${d.native.max_decision_seconds.toFixed(3)} 秒，比赛时限 10 秒。`;
  $("evaluation-note").textContent=d.evaluation_note+" "+d.demo_note;
}
async function exportSgf() {
  if(!state.game)return;
  const g=state.game;const path=g.readonly ? `/api/demo/replays/${g.replay_id}/sgf` : `/api/games/${g.id}/sgf`;
  const link=document.createElement("a");link.href=path;link.download=`xiaqi-${g.id}.sgf`;document.body.append(link);link.click();link.remove();
}

document.querySelectorAll("[data-page]").forEach((b)=>b.addEventListener("click",()=>page(b.dataset.page)));
document.querySelectorAll("[data-mode]").forEach((b)=>b.addEventListener("click",()=>switchMode(b.dataset.mode)));
document.querySelectorAll("[data-color]").forEach((b)=>b.addEventListener("click",()=>{state.color=b.dataset.color;syncMode();}));
$("start").addEventListener("click",start);
$("pass").addEventListener("click",()=>humanMove({action:"PASS"}));
$("resign").addEventListener("click",()=>humanMove({action:"RESIGN"}));
$("autoplay").addEventListener("click",toggleAuto);
$("step").addEventListener("click",step);
$("latest").addEventListener("click",()=>seek(state.game.frames.length-1));
$("timeline").addEventListener("input",(e)=>seek(Number(e.target.value)));
$("replay-filter").addEventListener("change",()=>{fillReplays();loadReplay(Number($("replay-select").value));});
$("replay-select").addEventListener("change",()=>loadReplay(Number($("replay-select").value)));
$("replay-speed").addEventListener("change",()=>{if(state.auto)schedule();});
$("moves").addEventListener("click",(e)=>{const b=e.target.closest("[data-frame]");if(b)seek(Number(b.dataset.frame));});
$("history").addEventListener("click",(e)=>{const b=e.target.closest("[data-history]");if(b)openHistory(b.dataset.history);});
$("game-grid").addEventListener("click",async(e)=>{const b=e.target.closest("[data-replay]");if(!b||state.busy)return;page("play");state.mode="replay";$("replay-filter").value="all";fillReplays();$("replay-select").value=b.dataset.replay;syncMode();await loadReplay(Number(b.dataset.replay));$("play-page").scrollIntoView({behavior:"smooth",block:"start"});});
$("export").addEventListener("click",exportSgf);
canvas.addEventListener("mousemove",(e)=>{state.hover=eventPoint(e);draw();});
canvas.addEventListener("mouseleave",()=>{state.hover=null;draw();});
canvas.addEventListener("focus",draw);canvas.addEventListener("blur",draw);
canvas.addEventListener("click",(e)=>{if(!humanCanPlay())return;const p=eventPoint(e);if(p&&legal(p))humanMove({action:"PLACE",...p});else if(p){state.error="这个交叉点当前不可落子，请选择其他位置。";render();}});
canvas.addEventListener("keydown",(e)=>{const moves={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,1],ArrowDown:[0,-1]};if(moves[e.key]){e.preventDefault();const[dx,dy]=moves[e.key];state.focus={x:Math.max(0,Math.min(8,state.focus.x+dx)),y:Math.max(0,Math.min(8,state.focus.y+dy))};draw();}else if(e.key==="Enter"||e.key===" "){e.preventDefault();if(humanCanPlay()&&legal(state.focus))humanMove({action:"PLACE",...state.focus});}});
window.addEventListener("resize",resize);new ResizeObserver(resize).observe(canvas.parentElement);
window.addEventListener("beforeunload",stop);
boot();
