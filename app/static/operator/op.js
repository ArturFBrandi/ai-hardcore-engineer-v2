"use strict";
const st = { token: sessionStorage.getItem("op_token"), selected: null };
const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };

async function api(path, opts = {}) {
  const headers = Object.assign({ "Content-Type": "application/json", Authorization: `Bearer ${st.token}` }, opts.headers || {});
  const r = await fetch(path, Object.assign({}, opts, { headers }));
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(`${r.status} ${body.error || ""}: ${body.detail || ""}`);
  return body;
}

async function loadQueue() {
  if (!st.token) return;
  const status = $("filter").value;
  const data = await api(`/v1/operator/cases${status ? `?status=${status}` : ""}`);
  const q = $("queue"); q.textContent = "";
  data.items.forEach((c) => {
    const it = el("div", `item${c.case_id === st.selected ? " active" : ""}`);
    const top = el("div", "row");
    top.appendChild(el("strong", "", c.case_id));
    top.appendChild(el("span", `small prio-${c.priority}`, c.priority));
    if (c.awaiting_human) top.appendChild(el("span", "chip esc", c.interrupt_kind === "approval" ? "needs approval" : "needs reply"));
    it.appendChild(top);
    it.appendChild(el("div", "small", c.subject));
    it.appendChild(el("div", "small muted", `${c.customer_id} · ${c.status} · SLA ${c.sla_due_at ? new Date(c.sla_due_at).toLocaleTimeString() : "-"}`));
    it.onclick = () => openCase(c.case_id);
    q.appendChild(it);
  });
}

function section(title, content) { const c = el("div", "card"); c.appendChild(el("h3", "", title)); if (typeof content === "string") c.appendChild(el("pre", "", content)); else c.appendChild(content); return c; }

async function act(path, body, label) {
  try { const out = await api(path, { method: "POST", body: JSON.stringify(body || {}) }); alert(`${label}: ${out.status || out.case_id || "ok"}${out.duplicate ? " (duplicate submit ignored)" : ""}`); }
  catch (e) { alert(String(e)); }
  await openCase(st.selected); await loadQueue();
}

async function openCase(id) {
  st.selected = id;
  const c = await api(`/v1/operator/cases/${id}`);
  const d = $("detail"); d.textContent = "";
  d.appendChild(section(`${c.case_id} · ${c.category} · ${c.priority} · ${c.status}`, `${c.subject}\n\n${c.description}\n\nReasons: ${(c.escalation_reasons || []).join("; ")}`));
  d.appendChild(section("Execution context (sanitized)", JSON.stringify(c.context, null, 2)));
  const ex = el("div");
  (c.conversation_excerpt || []).forEach((t) => ex.appendChild(el("div", `msg ${t.role}`, `${t.role}: ${t.content}`)));
  d.appendChild(section("Conversation excerpt", ex));
  if (c.proposed_action) d.appendChild(section("Proposed action (requires approval)", JSON.stringify(c.proposed_action, null, 2)));

  const actions = el("div");
  const row = el("div", "row");
  const claim = el("button", "secondary", "Claim"); claim.onclick = () => act(`/v1/operator/cases/${id}/claim`, {}, "Claimed"); row.appendChild(claim);
  actions.appendChild(row);
  if (c.awaiting_human && c.interrupt_kind === "reply") {
    const ta = el("textarea"); ta.placeholder = "Reply to the customer (will be rewritten politely by MiniMax-M3 unless 'send as is')";
    const asis = el("input"); asis.type = "checkbox"; const lab = el("label", "small", " send as is"); lab.prepend(asis);
    const send = el("button", "ok", "Send reply & resume workflow");
    send.onclick = () => act(`/v1/operator/cases/${id}/reply`, { message: ta.value, send_as_is: asis.checked }, "Reply");
    actions.append(ta, lab, send);
  }
  if (c.awaiting_human && c.interrupt_kind === "approval") {
    const note = el("textarea"); note.placeholder = "Note to the customer / reason";
    const args = el("textarea"); args.placeholder = '{"terminal_id": 1001} — only for "edit"';
    const r2 = el("div", "row");
    const ok = el("button", "ok", "Approve"); ok.onclick = () => act(`/v1/operator/cases/${id}/decision`, { decision: "approve", note: note.value }, "Approved");
    const ed = el("button", "secondary", "Approve with edits");
    ed.onclick = () => { let parsed; try { parsed = JSON.parse(args.value || "{}"); } catch (e) { alert("invalid JSON"); return; } act(`/v1/operator/cases/${id}/decision`, { decision: "edit", edited_arguments: parsed, note: note.value }, "Edited"); };
    const no = el("button", "danger", "Reject"); no.onclick = () => act(`/v1/operator/cases/${id}/decision`, { decision: "reject", note: note.value }, "Rejected");
    r2.append(ok, ed, no); actions.append(note, args, r2);
  }
  const r3 = el("div", "row"); const code = el("input"); code.type = "text"; code.value = "RESOLVED_BY_OPERATOR";
  const res = el("button", "secondary", "Resolve case"); res.onclick = () => act(`/v1/operator/cases/${id}/resolve`, { resolution_code: code.value, note: "" }, "Resolved");
  r3.append(code, res); actions.appendChild(r3);
  d.appendChild(section("Actions", actions));
  loadQueue();
}

$("login").onclick = async () => {
  const r = await fetch("/v1/dev/token", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sub: $("operator").value, scope: "cases:read cases:write approvals:write" }) });
  const body = await r.json();
  if (!r.ok) { alert(`Dev login unavailable: ${body.detail || r.status}`); return; }
  st.token = body.access_token; sessionStorage.setItem("op_token", st.token); loadQueue();
};
$("filter").onchange = loadQueue;
setInterval(() => loadQueue().catch(() => {}), 5000);
if (st.token) loadQueue().catch(() => {});
