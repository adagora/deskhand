"use strict";
const NAV = [["todo", "To do"], ["cases", "Cases"], ["authority", "Authority"], ["quality", "Quality"], ["health", "Health"]];
const TITLES = {
  todo: ["To do", "What needs a person now."],
  cases: ["Cases", "Every cancellation case, most urgent first."],
  authority: ["Authority", "Who may do what without a person, and until when."],
  quality: ["Quality", "How the agent did in the latest evaluation runs."],
  health: ["Health", "Whether the parts that run the cases are working."],
};
const S = { token: null, me: null, timer: null, todoCount: 0, busy: false };

function h(tag, attrs, ...kids) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "class") node.className = value;
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) node.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return node;
}

const money = (minor, currency) => (minor === null || minor === undefined ? "—" : `${(minor / 100).toFixed(2)} ${currency || "EUR"}`);
function when(iso) {
  if (!iso) return h("span", {}, "—");
  const date = new Date(iso), diff = date.getTime() - Date.now(), abs = Math.abs(diff);
  const units = [[86400000, "d"], [3600000, "h"], [60000, "min"]];
  let text = "now";
  for (const [size, name] of units) if (abs >= size) { text = `${Math.floor(abs / size)} ${name}`; break; }
  const label = abs < 60000 ? "just now" : diff > 0 ? `in ${text}` : `${text} ago`;
  return h("time", { datetime: iso, title: date.toLocaleString() }, label);
}
const pill = (state, text) => h("span", { class: `pill ${state}` }, { ok: "✔ ", warn: "▲ ", bad: "✖ ", unknown: "? " }[state] || "", text);
const short = (text) => (text ? `${text.slice(0, 12)}…` : "—");

async function api(path, options = {}) {
  const response = await fetch(path, {
    method: options.body ? "POST" : "GET",
    headers: { Authorization: `Bearer ${S.token}`, ...(options.body ? { "Content-Type": "application/json" } : {}) },
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const data = await response.json().catch(() => ({ ok: false, message: "The cockpit sent an unreadable answer.", next: "Reload the page." }));
  if (response.status === 401) { signOut(); throw Object.assign(new Error(data.message), { data }); }
  if (!response.ok) throw Object.assign(new Error(data.message), { data });
  return data;
}

function showError(error) {
  const box = document.getElementById("alert");
  const data = error.data || { message: error.message, next: "Try again." };
  box.replaceChildren(h("strong", {}, data.message || "Something went wrong."), data.next ? h("span", {}, data.next) : null);
  box.hidden = false;
}
const clearError = () => { document.getElementById("alert").hidden = true; };

function route() {
  const [page = "todo", id] = (location.hash.replace(/^#\/?/, "") || "todo").split("/");
  return { page: NAV.some(([key]) => key === page) ? page : "todo", id };
}

function renderNav(active) {
  document.getElementById("nav").replaceChildren(...NAV.map(([key, label]) =>
    h("a", { href: `#/${key}`, "aria-current": key === active ? "page" : null }, label,
      key === "todo" && S.todoCount ? h("span", { class: "badge", "aria-label": `${S.todoCount} waiting` }, S.todoCount) : null)));
  document.getElementById("who").textContent = S.me ? `${S.me.id} · ${S.me.roles.join(", ")}` : "";
}

function setHeading(title, subtitle) {
  document.getElementById("title").textContent = title;
  document.getElementById("subtitle").textContent = subtitle || "";
  document.title = `${title} · CityWheels cockpit`;
}

function signIn() {
  setHeading("Sign in", "Paste the token that `python -m orchestration seed` printed for your role.");
  document.getElementById("tools").hidden = true;
  document.getElementById("nav").replaceChildren();
  const input = h("input", { id: "token", type: "password", autocomplete: "off", required: true });
  const form = h("form", { class: "card", onsubmit: async (event) => {
    event.preventDefault();
    S.token = input.value.trim();
    try {
      const response = await fetch("/api/session", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token: S.token }) });
      const data = await response.json();
      if (!response.ok) throw Object.assign(new Error(data.message), { data });
      S.me = data; sessionStorage.setItem("cockpit-token", S.token); clearError(); render();
    } catch (error) { S.token = null; showError(error); }
  } },
    h("label", { for: "token" }, "Access token"), h("br"), input, h("p", { class: "note" }, "The token stays in this browser tab only."),
    h("button", { class: "btn primary", type: "submit" }, "Sign in"));
  document.getElementById("page").replaceChildren(form);
  input.focus();
}

function signOut() { S.token = null; S.me = null; sessionStorage.removeItem("cockpit-token"); clearInterval(S.timer); signIn(); }

async function render() {
  if (!S.token) return signIn();
  const { page, id } = route();
  document.getElementById("tools").hidden = false;
  const [title, subtitle] = id && page === "cases" ? ["Case", ""] : TITLES[page];
  setHeading(title, subtitle);
  renderNav(page);
  try {
    const node = await ({ todo: todoPage, cases: id ? () => casePage(id) : casesPage, authority: authorityPage, quality: qualityPage, health: healthPage }[page])();
    clearError();
    if (!S.busy) document.getElementById("page").replaceChildren(node);
    refreshBadge();
  } catch (error) { showError(error); }
}

async function refreshBadge() {
  try { S.todoCount = (await api("/api/todo")).items.length; renderNav(route().page); } catch { /* the page itself shows errors */ }
}

function empty(text) { return h("p", { class: "empty" }, text); }
const open = (caseId, label = "Open") => h("a", { class: "btn", href: `#/cases/${caseId}`, "aria-label": `${label} case ${caseId.slice(0, 8)}` }, label);

async function todoPage() {
  const { items } = await api("/api/todo");
  S.todoCount = items.length;
  const kinds = { review: ["bad", "Review"], exception: ["bad", "Exception"], stuck: ["warn", "Stuck?"], authority: ["warn", "Authority"], health: ["bad", "Health"] };
  return h("div", {}, h("section", { class: "card" }, h("h2", {}, items.length ? `${items.length} to do` : "Nothing needs a person"),
    items.length ? h("ul", { class: "list" }, items.map((item) => h("li", {},
      pill(...kinds[item.kind]),
      h("div", { class: "what" }, h("strong", {}, item.title), h("span", {}, item.detail), " ", item.due ? h("span", {}, "Due ", when(item.due)) : item.since ? h("span", {}, "Since ", when(item.since)) : null),
      item.case_id ? open(item.case_id, "Open case") : h("a", { class: "btn", href: item.kind === "health" ? "#/health" : "#/authority" }, "Open")))) : empty("No reviews are waiting, no exceptions are open and the system is healthy.")));
}

async function casesPage() {
  const { cases } = await api("/api/cases");
  const needs = { review: ["bad", "Needs your review"], staff: ["bad", "Needs a person"], stuck: ["warn", "Not moving"], customer: ["warn", "Waiting for the customer"] };
  const rows = cases.map((row) => h("tr", {},
    h("td", {}, h("a", { href: `#/cases/${row.case_id}` }, row.case_id.slice(0, 10) + "…")),
    h("td", {}, row.proposal ? row.proposal.booking_id : row.booking_id || "—"),
    h("td", {}, row.proposal ? money(row.proposal.amount_minor, row.proposal.currency) : row.result && row.result.amount_minor ? money(row.result.amount_minor, row.result.currency) : "—"),
    h("td", {}, row.needs ? pill(...needs[row.needs]) : pill(row.status === "RUNNING" ? "ok" : row.phase === "completed" ? "ok" : "unknown", row.phase_label)),
    h("td", {}, row.needs ? row.phase_label : ""),
    h("td", {}, when(row.started_at))));
  return h("section", { class: "card" }, h("h2", {}, `${cases.length} case${cases.length === 1 ? "" : "s"}`),
    cases.length ? h("div", { class: "table-wrap", tabindex: "0", role: "region", "aria-label": "Cases table" }, h("table", {},
      h("thead", {}, h("tr", {}, ["Case", "Booking", "Refund", "State", "Detail", "Started"].map((name) => h("th", { scope: "col" }, name)))), h("tbody", {}, rows))) : empty("No case has been opened yet. A case opens when a customer clearly asks to cancel."));
}

const decide = (id, action, body) => api(`/api/cases/${encodeURIComponent(id)}/${action}`, { body }).then((value) => { S.follow = { id, left: 20 }; return value; });

function confirmPanel(title, body, confirmLabel, onConfirm, danger) {
  const panel = h("div", { class: "panel", role: "group", "aria-label": title }, h("strong", {}, title), h("p", {}, body),
    h("div", { class: "row" },
      h("button", { class: `btn ${danger ? "danger" : "primary"}`, type: "button", onclick: async () => { try { await onConfirm(); S.busy = false; await render(); } catch (error) { showError(error); } } }, confirmLabel),
      h("button", { class: "btn", type: "button", onclick: () => { S.busy = false; render(); } }, "Cancel")));
  S.busy = true;
  document.getElementById("page").prepend(panel);
  panel.querySelector("button").focus();
}

async function casePage(id) {
  const c = await api(`/api/cases/${encodeURIComponent(id)}`);
  const p = c.proposal;
  const stateText = c.needs === "review" ? "Needs your review" : c.phase_label;
  const head = h("section", { class: "card" }, h("h2", {}, c.booking_id ? `Booking ${c.booking_id}` : "Case"),
    h("p", {}, pill(c.needs === "review" || c.needs === "staff" ? "bad" : c.status === "RUNNING" ? "warn" : "ok", stateText), " ", c.phase_label !== stateText ? c.phase_label : ""),
    h("dl", { class: "kv" },
      h("dt", {}, "Case"), h("dd", { class: "mono" }, c.case_id),
      h("dt", {}, "Refund"), h("dd", {}, p ? money(p.amount_minor, p.currency) : c.result && c.result.amount_minor ? money(c.result.amount_minor, c.result.currency) : "—"),
      p ? [h("dt", {}, "Paid with"), h("dd", {}, p.payment_kind), h("dt", {}, "Quote expires"), h("dd", {}, when(p.expires_at)),
        h("dt", {}, "Review"), h("dd", {}, p.review_required ? "Required: the refund is above the automatic limit." : "Not required."),
        h("dt", {}, "Quote digest"), h("dd", { class: "mono", title: p.digest }, short(p.digest))] : null,
      c.staff ? [h("dt", {}, "Exception"), h("dd", {}, `${c.staff.message} ${c.staff.next}`)] : null,
      c.result && c.result.code ? [h("dt", {}, "Reason"), h("dd", {}, c.result.code)] : null,
      h("dt", {}, "Opened"), h("dd", {}, when(c.started_at))));
  const settled = !c.can_review && c.needs !== "review" && c.phase !== "waiting-for-review" && !c.resolve_kinds.length;
  if (S.follow && S.follow.id === id && settled) S.follow = null;
  const following = Boolean(S.follow && S.follow.id === id);
  const nodes = [h("p", {}, h("a", { href: "#/cases" }, "← All cases"))];
  if (following) nodes.push(h("p", { class: "notice", role: "status" }, "Your decision was recorded. The case is moving on…"));
  nodes.push(head);
  if (c.can_review) {
    nodes.push(h("section", { class: "panel" }, h("strong", {}, "Your decision"),
      h("p", {}, `Approve or reject a refund of ${money(p.amount_minor, p.currency)} for booking ${p.booking_id}.`,
        c.phase === "waiting-for-consent" ? " The customer has not confirmed yet. Your review is kept, and the refund waits for the customer." : ""),
      h("div", { class: "row" },
        h("button", { class: "btn primary", type: "button", onclick: () => confirmPanel("Approve this refund?", `Refund ${money(p.amount_minor, p.currency)} to the customer's ${p.payment_kind}. (digest ${short(p.digest)}). This cannot be undone once the refund is issued.`, "Approve refund", () => decide(id, "review", { verdict: "approved", digest: p.digest })) }, "Approve…"),
        h("button", { class: "btn danger", type: "button", onclick: () => confirmPanel("Reject this refund?", "Nothing will be written. The customer has to ask again for a new quote.", "Reject", () => decide(id, "review", { verdict: "rejected", digest: p.digest }), true) }, "Reject…"))));
  }
  if (c.resolve_kinds.length) {
    nodes.push(h("section", { class: "panel" }, h("strong", {}, "Resolve this exception"), h("p", {}, c.staff ? c.staff.next : ""),
      h("div", { class: "row" }, c.resolve_kinds.map((kind) => h("button", { class: kind === "close" ? "btn" : "btn primary", type: "button",
        onclick: () => confirmPanel(kind === "close" ? "Close this exception?" : "Complete the refund?", kind === "close" ? "The case ends without a refund. Do this only when nothing is owed." : "A supervisor completes the refund after a recheck. There is no rollback.", kind === "close" ? "Close exception" : "Complete refund", () => decide(id, "resolve", { kind }), kind === "close") }, kind === "close" ? "Close exception…" : "Complete refund…")))));
  }
  nodes.push(h("section", { class: "card" }, h("h2", {}, "What happened"),
    c.timeline.length ? h("ol", { class: "timeline" }, c.timeline.map((e) => h("li", {}, h("time", { datetime: e.at, title: e.at }, new Date(e.at).toLocaleTimeString()), h("span", { class: `mark ${e.state}`, "aria-label": e.state === "ok" ? "done" : e.state === "warn" ? "note" : "failed" }, e.state === "ok" ? "✔" : e.state === "warn" ? "▲" : "✖"), h("div", {}, e.label, e.detail ? h("small", {}, e.detail) : null)))) : empty("No events yet.")));
  const ev = c.evidence;
  nodes.push(h("section", { class: "card" }, h("h2", {}, "Evidence"),
    !ev ? empty("No audit log is connected. Start the cockpit with --audit-log to see the model calls and decisions of this case.") :
    !c.correlation ? empty("This case has no correlation id, so its model calls cannot be matched. Cases opened with `orchestration ask` carry one.") :
    h("div", {}, h("p", {}, `${ev.calls.length} model call${ev.calls.length === 1 ? "" : "s"} · ${ev.decisions.length} decision${ev.decisions.length === 1 ? "" : "s"} · correlation `, h("code", {}, c.correlation)),
      ev.decisions.map((d) => h("div", {}, h("dl", { class: "kv" },
        h("dt", {}, "Decision"), h("dd", { class: "mono" }, d.id),
        h("dt", {}, "Reviews"), h("dd", {}, d.reviews.length ? d.reviews.map((r) => `${r.reviewer}: ${r.verdict}`).join("; ") : "None yet"),
        h("dt", {}, "Outcome"), h("dd", {}, d.effects.length ? d.effects.map((x) => `${x.action}: ${x.status}`).join("; ") : "Not recorded"),
        h("dt", {}, "Receipt"), h("dd", {}, h("code", { class: "cmd" }, d.receipt))))),
      ev.console_url ? h("p", {}, h("a", { href: ev.console_url, target: "_blank", rel: "noopener noreferrer" }, "Open the ai-audit console")) : null)));
  if (following) {
    if (S.follow.left-- <= 0) S.follow = null;
    else setTimeout(() => { if (!S.busy && route().id === id) render(); }, 1500);
  }
  return h("div", {}, nodes);
}

async function authorityPage() {
  const { grants, can_revoke: canRevoke } = await api("/api/authority");
  const states = { active: "ok", approved: "warn", expired: "bad", revoked: "bad" };
  const rows = grants.map((g) => h("tr", {},
    h("td", {}, g.intent), h("td", {}, pill(states[g.state] || "unknown", g.state)), h("td", {}, g.tier),
    h("td", {}, g.actions.join(", ")), h("td", {}, `up to ${money(g.max_amount_minor, g.currency)}`),
    h("td", {}, when(g.expires_at)), h("td", {}, `Approved by ${g.approved_by}${g.activated_by ? `, activated by ${g.activated_by}` : ""}${g.revoke_reason ? `. Revoked: ${g.revoke_reason}` : ""}`),
    h("td", {}, canRevoke && (g.state === "active" || g.state === "approved") ? h("button", { class: "btn danger", type: "button", "aria-label": `Revoke the grant for ${g.intent}`, onclick: () => {
      const reason = h("input", { id: "reason", type: "text", maxlength: "300", required: true, "aria-label": "Reason for revoking" });
      confirmPanel("Revoke this grant?", "The operation falls back to a person until a new grant is activated.", "Revoke", async () => { if (!reason.value.trim()) throw Object.assign(new Error("A reason is needed."), { data: { message: "A reason is needed.", next: "Say why you are revoking the grant." } }); await api(`/api/authority/${g.id}/revoke`, { body: { reason: reason.value } }); }, true);
      document.querySelector(".panel p").after(reason); reason.focus();
    } }, "Revoke…") : null)));
  return h("section", { class: "card" }, h("h2", {}, `${grants.length} grant${grants.length === 1 ? "" : "s"}`),
    h("p", {}, "A grant lets one operation run without a person up to a limit. Tiers go shadow, assist, contain, transact; only transact lets the desk write."),
    grants.length ? h("div", { class: "table-wrap", tabindex: "0", role: "region", "aria-label": "Grants table" }, h("table", {},
      h("thead", {}, h("tr", {}, ["Operation", "State", "Tier", "Actions", "Limit", "Expires", "History", ""].map((name) => h("th", { scope: "col" }, name)))), h("tbody", {}, rows))) : empty("No grant exists, so every cancellation waits for a person."));
}

async function qualityPage() {
  const { configured, runs } = await api("/api/quality");
  if (!configured) return h("section", { class: "card" }, h("h2", {}, "No evaluation folder"), empty("Start the cockpit with --evals-dir pointing at the IntentGauge runs folder."));
  const rows = runs.map((r) => h("tr", {},
    h("td", {}, when(r.created_at)), h("td", {}, r.mode),
    h("td", {}, `${r.passed_trials} of ${r.trials}`, h("div", { class: "note" }, `${r.cases} cases × ${r.repetitions}`)),
    h("td", {}, r.pass_rate_lower_bound === null || r.pass_rate_lower_bound === undefined ? "—" : `${Math.round(r.pass_rate_lower_bound * 100)}%`),
    h("td", {}, r.critical_failures ? pill("bad", `${r.critical_failures} critical`) : pill("ok", "none")),
    h("td", {}, r.flaky_cases.length ? r.flaky_cases.join(", ") : "—"), h("td", {}, r.failing_cases.length ? r.failing_cases.join(", ") : "—"),
    h("td", {}, r.dashboard ? h("a", { href: `/evals/${encodeURIComponent(r.run)}/${r.dashboard}`, target: "_blank", rel: "noopener noreferrer" }, "Open") : "—")));
  return h("div", {}, h("section", { class: "card" }, h("h2", {}, "Not qualified"), h("p", {}, "These runs use development cases and no human-scored evidence. A passing run shows the checks held; it does not show the agent is ready to act alone.")),
    h("section", { class: "card" }, h("h2", {}, "Latest runs"),
      runs.length ? h("div", { class: "table-wrap", tabindex: "0", role: "region", "aria-label": "Evaluation runs table" }, h("table", {},
        h("thead", {}, h("tr", {}, ["When", "Model", "Trials passed", "Lower bound (95%)", "Critical failures", "Flaky cases", "Always failing", "Report"].map((name) => h("th", { scope: "col" }, name)))), h("tbody", {}, rows))) : empty("No evaluation run for this application was found.")));
}

async function healthPage() {
  const { overall, checks } = await api("/api/health");
  const map = { ok: "ok", warn: "warn", bad: "bad", unknown: "unknown" };
  return h("section", { class: "card" }, h("h2", {}, overall === "ok" ? "Everything is working" : overall === "bad" ? "Something needs fixing" : "Check the warnings"),
    h("ul", { class: "list" }, checks.map((check) => h("li", {}, pill(map[check.state], { ok: "OK", warn: "Warning", bad: "Problem", unknown: "Unknown" }[check.state]), h("div", { class: "what" }, h("strong", {}, check.label), h("span", {}, check.detail))))));
}

document.getElementById("refresh").addEventListener("click", () => { clearError(); render(); });
document.getElementById("auto").addEventListener("change", schedule);
async function adopt(token) {
  S.token = token;
  try {
    S.me = await api("/api/me");
    await fetch("/api/session", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token }) });
    sessionStorage.setItem("cockpit-token", token);
  } catch { S.token = null; S.me = null; }
}
window.addEventListener("hashchange", async () => {
  clearError(); S.busy = false;
  const given = new URLSearchParams(location.hash.replace(/^#/, "")).get("token");
  if (given) { history.replaceState(null, "", location.pathname); await adopt(given); }
  render().then(() => { window.scrollTo(0, 0); document.getElementById("main").focus({ preventScroll: true }); });
});
function schedule() {
  clearInterval(S.timer);
  if (document.getElementById("auto").checked) S.timer = setInterval(() => { if (!S.busy && S.token) render(); }, 15000);
}

(async function start() {
  const given = new URLSearchParams(location.hash.replace(/^#/, "")).get("token");
  if (given) history.replaceState(null, "", location.pathname);
  const token = given || sessionStorage.getItem("cockpit-token");
  if (token) await adopt(token);
  schedule();
  render();
})();
