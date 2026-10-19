"use strict";
const state = { token: sessionStorage.getItem("token"), sub: sessionStorage.getItem("sub"), conv: null, es: null };
const $ = (id) => document.getElementById(id);

async function api(path, opts = {}) {
  const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const r = await fetch(path, Object.assign({}, opts, { headers }));
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(`${r.status} ${body.error || ""}: ${body.detail || ""}`);
  return body;
}

function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }

function renderTurn(t) {
  const box = el("div", `msg ${t.role}`);
  box.appendChild(el("div", "", t.content));
  const meta = el("div", "meta");
  if (t.role === "assistant") {
    if (t.intent) meta.appendChild(el("span", "chip", t.intent));
    (t.agents || []).forEach((a) => meta.appendChild(el("span", "chip", a)));
    if (t.case_id) meta.appendChild(el("span", "chip esc", `case ${t.case_id}`));
  }
  if (t.role === "human_operator") meta.appendChild(el("span", "chip ok", "Getnet specialist"));
  if (t.role === "system_note") meta.appendChild(el("span", "chip", "status update"));
  box.appendChild(meta);
  if ((t.sources || []).length) {
    const s = el("div", "meta sources");
    t.sources.forEach((src, i) => { const a = el("a", "", `[${i + 1}] ${src.title}`); a.href = src.url; a.target = "_blank"; a.rel = "noopener noreferrer"; s.appendChild(a); });
    box.appendChild(s);
  }
  return box;
}

function renderLive(resp) {
  const box = renderTurn({ role: "assistant", content: resp.answer, intent: resp.intent, agents: resp.agents, case_id: resp.case_id, sources: resp.sources });
  const meta = el("div", "meta");
  meta.appendChild(el("span", "chip", `route: ${resp.route_source}`));
  meta.appendChild(el("span", "chip", `confidence ${resp.confidence}`));
  meta.appendChild(el("span", resp.escalated ? "chip esc" : "chip ok", resp.status));
  if (resp.token_usage && resp.token_usage.llm_calls !== undefined) meta.appendChild(el("span", "chip", `${resp.token_usage.llm_calls} LLM calls · $${resp.token_usage.usd}`));
  (resp.escalation_reasons || []).forEach((r) => meta.appendChild(el("span", "chip esc", r)));
  box.appendChild(meta);
  return box;
}

async function loadConversations() {
  const list = $("conversations"); list.textContent = "";
  const data = await api("/v1/conversations?limit=30");
  data.items.forEach((c) => {
    const it = el("div", `item${c.conversation_id === state.conv ? " active" : ""}`);
    it.appendChild(el("div", "small", c.conversation_id));
    it.appendChild(el("div", "small muted", `${c.status} · ${new Date(c.updated_at).toLocaleString()}`));
    it.onclick = () => openConversation(c.conversation_id);
    list.appendChild(it);
  });
}

async function openConversation(id) {
  state.conv = id;
  const data = await api(`/v1/conversations/${id}`);
  const box = $("messages"); box.textContent = "";
  data.turns.forEach((t) => box.appendChild(renderTurn(t)));
  box.scrollTop = box.scrollHeight;
  subscribe(id);
  loadConversations();
}

function subscribe(id) {
  if (state.es) state.es.close();
  state.es = new EventSource(`/v1/conversations/${id}/events?access_token=${encodeURIComponent(state.token)}`);
  const refresh = () => openConversationQuiet(id);
  ["human_reply", "case_status"].forEach((ev) => state.es.addEventListener(ev, refresh));
}

async function openConversationQuiet(id) {
  if (id !== state.conv) return;
  const data = await api(`/v1/conversations/${id}`);
  const box = $("messages"); box.textContent = "";
  data.turns.forEach((t) => box.appendChild(renderTurn(t)));
  box.scrollTop = box.scrollHeight;
}

$("login").onclick = async () => {
  const sub = $("who").value;
  const r = await fetch("/v1/dev/token", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sub, scope: "chat conversations:read conversations:delete" }) });
  const body = await r.json();
  if (!r.ok) { alert(`Dev login unavailable: ${body.detail || r.status}`); return; }
  state.token = body.access_token; state.sub = sub; state.conv = null;
  sessionStorage.setItem("token", state.token); sessionStorage.setItem("sub", sub);
  $("messages").textContent = ""; loadConversations();
};

$("new").onclick = () => { state.conv = null; if (state.es) state.es.close(); $("messages").textContent = ""; };

$("composer").onsubmit = async (e) => {
  e.preventDefault();
  const text = $("text").value.trim(); if (!text || !state.token) return;
  $("send").disabled = true;
  const box = $("messages");
  box.appendChild(renderTurn({ role: "customer", content: text }));
  $("text").value = "";
  try {
    const payload = { message: text };
    if (state.conv) payload.conversation_id = state.conv;
    const resp = await api("/v1/chat", { method: "POST", body: JSON.stringify(payload), headers: { "Idempotency-Key": `ui-${crypto.randomUUID()}` } });
    box.appendChild(renderLive(resp));
    if (state.conv !== resp.conversation_id) { state.conv = resp.conversation_id; subscribe(state.conv); }
    loadConversations();
  } catch (err) { box.appendChild(el("div", "msg system_note", String(err))); }
  finally { $("send").disabled = false; box.scrollTop = box.scrollHeight; }
};

if (state.sub) $("who").value = state.sub;
if (state.token) loadConversations().catch(() => { state.token = null; });
