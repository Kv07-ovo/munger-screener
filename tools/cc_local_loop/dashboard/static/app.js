"use strict";
// cc_local_loop Dashboard — native JS, read-only. Polls /api/status every 2s and
// pulls heavier endpoints on change or every ~6s. No third-party libraries.

const PIPELINE = ["start", "round", "implement", "collect", "review", "decide", "finish"];
const STATUS_BADGE = {
  STARTED: "active", ROUND_READY: "active", ROUND_PENDING: "active",
  COLLECTED: "active", REVIEWED: "active",
  WAITING_HUMAN_ACCEPTANCE: "done", WAITING_HUMAN_DECISION: "wait",
  MAX_ROUNDS_REACHED: "stop", FINISHED: "done",
};

let state = { task_id: null, updated_at: null, status: null };
let backoff = 2000;
let curLog = "hook";
let lastHeavy = 0;

const $ = (id) => document.getElementById(id);
const txt = (el, s) => { if (el) el.textContent = (s === null || s === undefined || s === "") ? "—" : String(s); };

function esc(s) {
  return String(s === null || s === undefined ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function clip(s, n) { s = String(s || ""); return s.length > n ? s.slice(0, n) + "…" : s; }

function setConn(kind, label) {
  const c = $("conn");
  c.className = "conn conn-" + kind;
  c.textContent = label;
}

async function getJSON(url) {
  const r = await fetch(url, { headers: { "Accept": "application/json" } });
  if (!r.ok) throw new Error("HTTP " + r.status);
  return r.json();
}

function renderPipeline(step) {
  const ol = $("pipeline");
  ol.innerHTML = "";
  PIPELINE.forEach((name, i) => {
    const li = document.createElement("li");
    let cls = "pending";
    if (step !== null && step !== undefined) {
      if (i < step) cls = "done";
      else if (i === step) cls = "active";
    }
    li.className = cls;
    li.textContent = (i + 1) + ". " + name;
    ol.appendChild(li);
  });
}

function badge(status) {
  const b = $("statusBadge");
  const kind = STATUS_BADGE[status] || "idle";
  b.className = "badge badge-" + kind;
  b.textContent = status || "no task";
}

async function pollStatus() {
  try {
    const s = await getJSON("/api/status");
    setConn("ok", "connected");
    backoff = 2000;
    txt($("refreshTime"), (s.server_time || "").replace("T", " "));
    badge(s.status);
    renderPipeline(s.stage ? s.stage.step : null);

    const changed = s.task_id !== state.task_id || s.updated_at !== state.updated_at
      || s.status !== state.status;
    state = { task_id: s.task_id, updated_at: s.updated_at, status: s.status };

    if (!s.active) {
      $("overview").innerHTML = '<div class="empty">no active task</div>';
    }
    const now = Date.now();
    if (changed || (now - lastHeavy) > 6000) {
      lastHeavy = now;
      refreshHeavy();
    }
  } catch (e) {
    setConn("bad", "disconnected");
    backoff = Math.min(backoff * 1.6, 30000);
  } finally {
    setTimeout(pollStatus, backoff);
  }
}

async function refreshHeavy() {
  pullCurrent();
  pullDiff();
  pullRuns();
  pullLog();
}

async function pullCurrent() {
  try {
    const c = await getJSON("/api/current");
    if (!c.active) {
      $("overview").innerHTML = '<div class="empty">no active task</div>';
      $("review").innerHTML = '<div class="empty">—</div>';
      $("artifacts").innerHTML = '<li class="empty">—</li>';
      return;
    }
    const st = c.state || {};
    const ov = [
      ["task_id", st.task_id], ["description", st.task_description],
      ["branch", st.branch], ["base_commit", clip(st.base_commit, 12)],
      ["round", (st.round_index || "?") + " / " + (st.max_rounds || "?")],
      ["status", st.status], ["last_verdict", st.last_verdict],
      ["backend", st.backend], ["created", st.created_at], ["updated", st.updated_at],
    ];
    $("overview").innerHTML = ov.map(
      ([k, v]) => `<div class="k">${esc(k)}</div><div class="v">${esc(v === null || v === undefined ? "—" : v)}</div>`
    ).join("");

    const a = c.round_artifacts || {};
    const artRows = Object.keys(a).map(
      (n) => `<li><span>round_${String(c.current_round).padStart(2, "0")}/${esc(n)}</span>` +
        `<span class="${a[n] ? "yes" : "no"}">${a[n] ? "present" : "—"}</span></li>`
    );
    artRows.push(`<li><span>final_report.md</span><span class="${c.has_final_report ? "yes" : "no"}">${c.has_final_report ? "present" : "—"}</span></li>`);
    artRows.push(`<li><span>task.md</span><span class="${c.has_task_card ? "yes" : "no"}">${c.has_task_card ? "present" : "—"}</span></li>`);
    $("artifacts").innerHTML = artRows.join("");

    pullReview(st.task_id, c.current_round);
  } catch (e) { /* keep last view */ }
}

async function pullReview(task, round) {
  try {
    const r = await getJSON("/api/review?task=" + encodeURIComponent(task) + "&round=" + encodeURIComponent(round));
    if (!r.exists) { $("review").innerHTML = '<div class="empty">no review.md for current round</div>'; return; }
    const v = r.review || {};
    const ev = (v.evidence || []).map((x) => `<li>${esc(x)}</li>`).join("") || "<li class='empty'>—</li>";
    const rk = (v.risks || []).map((x) => `<li>${esc(x)}</li>`).join("") || "<li class='empty'>—</li>";
    $("review").innerHTML =
      `<div class="review-meta">` +
      `<span><b>backend</b> ${esc(v.backend || "—")}</span>` +
      `<span><b>model</b> ${esc(v.model || "—")}</span>` +
      `<span><b>verdict</b> <span class="verdict ${esc(v.verdict || "UNSURE")}">${esc(v.verdict || "—")}</span></span>` +
      `</div>` +
      `<div class="subh">evidence</div><ul class="bullets">${ev}</ul>` +
      `<div class="subh">risks</div><ul class="bullets">${rk}</ul>` +
      `<div class="subh">next prompt</div><div class="muted">${esc(clip(v.next_prompt_summary, 600)) || "—"}</div>`;
  } catch (e) { /* keep */ }
}

async function pullDiff() {
  try {
    const d = await getJSON("/api/diff");
    txt($("gitHead"), d.head);
    const lines = (d.git_status || []).map((e) => `${e.code} ${e.path}`).join("\n");
    txt($("gitStatus"), lines || "(clean)");
    txt($("gitDiff"), d.diff_stat || "(no diff)");
  } catch (e) { /* keep */ }
}

async function pullRuns() {
  try {
    const data = await getJSON("/api/runs");
    const runs = data.runs || [];
    if (!runs.length) { $("runs").innerHTML = '<li class="empty">no runs</li>'; return; }
    $("runs").innerHTML = runs.map((r) =>
      `<li class="${r.is_active ? "active-run" : ""}">` +
      `<span class="tid">${esc(clip(r.task_id, 60))}</span>` +
      `<span class="tag">${esc(r.kind)} · ${esc(r.status || "—")} · r${esc(r.round_index || "?")}${r.is_active ? " · ACTIVE" : ""}</span>` +
      `</li>`
    ).join("");
  } catch (e) { /* keep */ }
}

async function pullLog() {
  if (!state.task_id) { txt($("logBody"), "—"); return; }
  try {
    const round = 1;
    const r = await getJSON("/api/logs?task=" + encodeURIComponent(state.task_id) +
      "&round=" + round + "&which=" + encodeURIComponent(curLog));
    if (!r.exists) { txt($("logBody"), "(no " + curLog + " log)"); return; }
    txt($("logBody"), r.content || "(empty)");
  } catch (e) { /* keep */ }
}

function initTabs() {
  document.querySelectorAll("#logTabs .tab").forEach((b) => {
    b.addEventListener("click", () => {
      document.querySelectorAll("#logTabs .tab").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      curLog = b.getAttribute("data-w");
      pullLog();
    });
  });
}

async function init() {
  try { const h = await getJSON("/api/health"); txt($("ver"), "v" + h.version + " · " + h.bind); } catch (e) { /* ignore */ }
  initTabs();
  renderPipeline(null);
  pollStatus();
}

init();
