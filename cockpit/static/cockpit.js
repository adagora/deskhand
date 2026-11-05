"use strict";
const BRAND = "Deskhand";
const NAV = [["todo", "To do"], ["cases", "Cases"], ["conversations", "Conversations"], ["authority", "Authority"], ["rules", "Rules"], ["quality", "Quality"], ["health", "Health"]];
const TITLES = {
  todo: ["To do", "What needs a person now."],
  cases: ["Cases", "Every cancellation case, most urgent first."],
  conversations: ["Conversations", "Questions that did not open a case."],
  authority: ["Authority", "Who may do what without a person, and until when."],
  rules: ["Rules", "What this build enforces. Read-only."],
  quality: ["Quality", "How the agent did in the latest evaluation runs."],
  health: ["Health", "Whether the parts that run the cases are working."],
};
const SVG_NS = "http://www.w3.org/2000/svg";
const ICONS = {
  todo: [["circle", { cx: 12, cy: 12, r: 9 }], ["path", { d: "M8 12.5l2.7 2.7L16 9.5" }]],
  cases: [["path", { d: "M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01" }]],
  conversations: [["path", { d: "M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z" }]],
  authority: [["path", { d: "M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6l7-3z" }], ["path", { d: "M9.5 12l2 2 3.5-4" }]],
  rules: [["path", { d: "M4 6h7M15 6h5M4 12h3M11 12h9M4 18h9M17 18h3" }], ["circle", { cx: 13, cy: 6, r: 2 }], ["circle", { cx: 9, cy: 12, r: 2 }], ["circle", { cx: 15, cy: 18, r: 2 }]],
  quality: [["path", { d: "M5 20V10M12 20V4M19 20v-7" }]],
  health: [["path", { d: "M3 12h4l2-6 4 12 2-6h6" }]],
  sun: [["circle", { cx: 12, cy: 12, r: 4 }], ["path", { d: "M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" }]],
  moon: [["path", { d: "M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z" }]],
  auto: [["circle", { cx: 12, cy: 12, r: 9 }], ["path", { d: "M12 3a9 9 0 0 1 0 18z", fill: "currentColor" }]],
  inbox: [["path", { d: "M4 13l2.5-7h11L20 13M4 13v6h16v-6M4 13h5l1 2h4l1-2h5" }]],
};
const THEMES = ["auto", "light", "dark"];
const S = { token: null, me: null, timer: null, todoCount: 0, busy: false, follow: null, run: null, shown: "", theme: "auto", replays: {} };

function h(tag, attrs, ...kids) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "class") node.className = value;
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const kid of kids.flat(Infinity)) if (kid !== null && kid !== undefined && kid !== false) node.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return node;
}

function icon(name) {
  const svg = document.createElementNS(SVG_NS, "svg");
  for (const [key, value] of Object.entries({ viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": "1.8", "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", class: "icon" })) svg.setAttribute(key, value);
  for (const [tag, attrs] of ICONS[name] || []) {
    const element = document.createElementNS(SVG_NS, tag);
    for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, String(value));
    svg.append(element);
  }
  return svg;
}

const money = (minor, currency) => (minor === null || minor === undefined ? "—" : `${(minor / 100).toFixed(2)} ${currency || "EUR"}`);
function when(iso) {
  if (!iso) return h("span", {}, "—");
  const date = new Date(iso), diff = date.getTime() - Date.now(), abs = Math.abs(diff);
  let text = "now";
  for (const [size, name] of [[86400000, "d"], [3600000, "h"], [60000, "min"]]) if (abs >= size) { text = `${Math.floor(abs / size)} ${name}`; break; }
  const label = abs < 60000 ? "just now" : diff > 0 ? `in ${text}` : `${text} ago`;
  return h("time", { datetime: iso, title: date.toLocaleString() }, label);
}
const pill = (tone, text) => h("span", { class: `pill ${tone}` }, text);
const short = (text) => (text ? `${text.slice(0, 12)}…` : "—");
function cell(label, ...kids) {
  const content = kids.flat(Infinity).filter((kid) => kid !== null && kid !== undefined && kid !== false && kid !== "");
  return content.length ? h("td", { "data-label": label }, h("div", { class: "v" }, content)) : h("td", { "data-label": label });
}
const CLOSED_TONE = { completed: "ok", already_settled: "ok", closed_by_staff: "ok", rejected: "warn", refused: "warn", quote_expired: "warn", ended: "unknown" };
const NEEDS = { review: ["bad", "Needs your review"], staff: ["bad", "Needs a person"], stuck: ["warn", "Not moving"], customer: ["info", "Waiting for the customer"] };
const stateOf = (row) => (row.needs ? NEEDS[row.needs] : [row.status === "RUNNING" ? "info" : CLOSED_TONE[row.phase] || "unknown", row.phase_label]);

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

async function working(button, work) {
  const label = button.textContent;
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try { return await work(); } finally { button.disabled = false; button.removeAttribute("aria-busy"); button.textContent = label; }
}

function loadTheme() { try { return THEMES.includes(localStorage.getItem("deskhand-theme")) ? localStorage.getItem("deskhand-theme") : "auto"; } catch { return "auto"; } }
function applyTheme() {
  if (S.theme === "auto") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = S.theme;
  const toggle = document.getElementById("theme");
  toggle.replaceChildren(icon({ auto: "auto", light: "sun", dark: "moon" }[S.theme]), h("span", {}, `Theme: ${S.theme}`));
  toggle.setAttribute("aria-label", `Theme: ${S.theme}. Change it.`);
}
function cycleTheme() {
  S.theme = THEMES[(THEMES.indexOf(S.theme) + 1) % THEMES.length];
  try { localStorage.setItem("deskhand-theme", S.theme); } catch { /* the choice just does not persist */ }
  applyTheme();
}

function route() {
  const [page = "todo", id] = (location.hash.replace(/^#\/?/, "") || "todo").split("/");
  return { page: NAV.some(([key]) => key === page) ? page : "todo", id };
}

function renderNav(active) {
  document.getElementById("nav").replaceChildren(...NAV.map(([key, label]) =>
    h("a", { href: `#/${key}`, "aria-current": key === active ? "page" : null }, icon(key), h("span", { class: "label" }, label),
      key === "todo" && S.todoCount ? h("span", { class: "badge", "aria-label": `${S.todoCount} waiting` }, S.todoCount) : null)));
  document.getElementById("who").replaceChildren(...(S.me ? [h("strong", {}, S.me.id), S.me.roles.join(", ")] : []));
}

function setHeading(title, subtitle) {
  document.getElementById("title").textContent = title;
  document.getElementById("subtitle").textContent = subtitle || "";
  document.title = `${title} · ${BRAND}`;
}

function signIn() {
  document.body.classList.add("signed-out");
  setHeading("Sign in", "");
  document.getElementById("tools").hidden = true;
  document.getElementById("nav").replaceChildren();
  const input = h("input", { id: "token", type: "password", autocomplete: "off", required: true });
  const form = h("form", { class: "card signin", onsubmit: async (event) => {
    event.preventDefault();
    S.token = input.value.trim();
    try {
      const response = await fetch("/api/session", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token: S.token }) });
      const data = await response.json();
      if (!response.ok) throw Object.assign(new Error(data.message), { data });
      S.me = data; sessionStorage.setItem("cockpit-token", S.token); clearError(); render();
    } catch (error) { S.token = null; showError(error); }
  } },
    h("h2", {}, `Sign in to ${BRAND}`),
    h("p", {}, "Paste the token that `python -m orchestration seed` printed for your role. It stays in this browser tab only."),
    h("label", { for: "token" }, "Access token"), input,
    h("button", { class: "btn primary", type: "submit" }, "Sign in"));
  document.getElementById("page").replaceChildren(h("div", { class: "signin-wrap" }, form));
  input.focus();
}

function signOut() { S.token = null; S.me = null; S.shown = ""; sessionStorage.removeItem("cockpit-token"); clearInterval(S.timer); signIn(); }

const skeleton = () => h("div", { class: "skeleton", "aria-hidden": "true" }, h("i"), h("i"), h("i"));

async function render() {
  if (!S.token) return signIn();
  document.body.classList.remove("signed-out");
  const { page, id } = route();
  const key = `${page}/${id || ""}`;
  document.getElementById("tools").hidden = false;
  const [title, subtitle] = id && page === "cases" ? ["Case", ""] : TITLES[page];
  setHeading(title, subtitle);
  renderNav(page);
  const pageBox = document.getElementById("page");
  if (S.shown !== key) { pageBox.replaceChildren(skeleton()); S.shown = key; }
  pageBox.setAttribute("aria-busy", "true");
  try {
    const node = await ({ todo: todoPage, cases: id ? () => casePage(id) : casesPage, conversations: conversationsPage, authority: authorityPage, rules: rulesPage, quality: qualityPage, health: healthPage }[page])();
    clearError();
    if (!S.busy) pageBox.replaceChildren(node);
    refreshBadge();
  } catch (error) { showError(error); }
  pageBox.removeAttribute("aria-busy");
}

async function refreshBadge() {
  try { S.todoCount = (await api("/api/todo")).items.length; renderNav(route().page); } catch { /* the page itself shows errors */ }
}

function empty(title, hint) {
  return h("div", { class: "empty" }, icon("inbox"), h("div", {}, h("strong", {}, title), hint ? h("div", { class: "note" }, hint) : null));
}
const openCase = (caseId, label = "Open case", primary = false) => h("a", { class: `btn ${primary ? "primary" : ""}`, href: `#/cases/${caseId}`, "aria-label": `${label} ${caseId.slice(0, 8)}` }, label);
function table(label, headers, rows) {
  return h("div", { class: "table-wrap", tabindex: "0", role: "region", "aria-label": label }, h("table", {},
    h("thead", {}, h("tr", {}, headers.map((header) => h("th", { scope: "col", class: header.endsWith("*") ? "amount" : null }, header.replace(/\*$/, ""))))), h("tbody", {}, rows)));
}
function goOnClick(hash) {
  return (event) => { if (!event.target.closest("a, button")) location.hash = hash; };
}

async function todoPage() {
  const { items } = await api("/api/todo");
  S.todoCount = items.length;
  const kinds = { review: ["bad", "Review"], exception: ["bad", "Exception"], stuck: ["warn", "Stuck?"], authority: ["warn", "Authority"], health: ["bad", "Health"] };
  return h("section", { class: "card" }, h("h2", {}, items.length ? `${items.length} to do` : "All clear"),
    items.length ? h("ul", { class: "list" }, items.map((item) => h("li", {},
      h("div", { class: "lead" }, pill(...kinds[item.kind])),
      h("div", { class: "what" }, h("strong", {}, item.title), h("span", {}, item.detail), " ", item.due ? h("span", { class: "note" }, "Due ", when(item.due)) : item.since ? h("span", { class: "note" }, "Since ", when(item.since)) : null),
      item.case_id ? openCase(item.case_id, item.kind === "review" ? "Review" : "Open case", item.kind === "review") : h("a", { class: "btn", href: item.kind === "health" ? "#/health" : "#/authority" }, "Open")))) :
      empty("Nothing needs a person", "No reviews are waiting, no exceptions are open and the system is healthy."));
}

async function casesPage() {
  const { cases } = await api("/api/cases");
  const closed = (await api("/api/staff").catch(() => ({ items: [] }))).items;
  const rows = cases.map((row) => {
    const [tone, label] = stateOf(row), hash = `#/cases/${row.case_id}`;
    const booking = row.proposal ? row.proposal.booking_id : row.booking_id;
    const refund = row.proposal ? money(row.proposal.amount_minor, row.proposal.currency) : row.result && row.result.amount_minor ? money(row.result.amount_minor, row.result.currency) : "—";
    return h("tr", { class: "clickable", onclick: goOnClick(hash) },
      cell("Booking", h("a", { href: hash }, booking || "Case")),
      h("td", { class: "amount", "data-label": "Refund" }, refund),
      cell("State", pill(tone, label)),
      cell("Detail", row.needs ? row.phase_label : ""),
      cell("Started", when(row.started_at)),
      cell("Case", h("span", { class: "small-id" }, row.case_id.slice(0, 10) + "…")));
  });
  const history = closed.length ? h("section", { class: "card" }, h("h2", {}, `${closed.length} closed exception${closed.length === 1 ? "" : "s"}`),
    h("ul", { class: "list" }, closed.map((item) => h("li", {}, h("div", { class: "what" }, h("strong", {}, item.explanation.message), h("span", {}, `Owner ${item.owner}. Closed by ${item.closed_by || "?"} (${item.closed_how || "?"}) `, when(item.closed_at))), openCase(item.case_id))))) : null;
  return h("div", {}, h("section", { class: "card" }, h("h2", {}, `${cases.length} case${cases.length === 1 ? "" : "s"}`),
    cases.length ? table("Cases table", ["Booking", "Refund*", "State", "Detail", "Started", "Case"], rows) : empty("No case yet", "A case opens when a customer clearly asks to cancel.")), history);
}

const decide = (id, action, body) => api(`/api/cases/${encodeURIComponent(id)}/${action}`, { body }).then((value) => { S.follow = { id, left: 20 }; return value; });

function confirmPanel(title, body, confirmLabel, onConfirm, danger) {
  clearError();
  const cancel = () => { S.busy = false; render(); };
  const go = h("button", { class: `btn ${danger ? "danger" : "primary"}`, type: "button", onclick: async () => {
    go.textContent = "Working…";
    try { await working(go, onConfirm); S.busy = false; await render(); } catch (error) { go.textContent = confirmLabel; showError(error); }
  } }, confirmLabel);
  const panel = h("div", { class: "panel confirm", role: "group", "aria-label": title, onkeydown: (event) => { if (event.key === "Escape") cancel(); } },
    h("strong", {}, title), h("p", {}, body), h("div", { class: "row" }, go, h("button", { class: "btn", type: "button", onclick: cancel }, "Cancel")));
  S.busy = true;
  document.getElementById("page").prepend(panel);
  go.focus();
}

async function casePage(id) {
  const c = await api(`/api/cases/${encodeURIComponent(id)}`);
  const p = c.proposal;
  const [tone, label] = stateOf(c);
  const refund = p ? money(p.amount_minor, p.currency) : c.result && c.result.amount_minor ? money(c.result.amount_minor, c.result.currency) : null;
  const head = h("section", { class: "card" },
    h("div", { class: "summary" }, h("div", {}, h("h2", {}, c.booking_id ? `Booking ${c.booking_id}` : "Case"), h("div", { class: "small-id" }, c.case_id)), pill(tone, label)),
    c.phase_label !== label ? h("p", { class: "note" }, c.phase_label) : null,
    refund ? h("div", { class: "amount-big" }, refund.replace(/ [A-Z]{3}$/, ""), h("small", {}, `${p ? p.currency : c.result.currency} to the customer's ${p ? p.payment_kind : c.result.payment_kind || "payment method"}`)) : null,
    h("dl", { class: "facts" },
      p ? [h("div", {}, h("dt", {}, "Quote expires"), h("dd", {}, when(p.expires_at))),
        h("div", {}, h("dt", {}, "Review"), h("dd", {}, p.review_required ? "Required: above the automatic limit" : "Not required")),
        h("div", {}, h("dt", {}, "Quote digest"), h("dd", { class: "mono", title: p.digest }, short(p.digest)))] : null,
      c.staff ? h("div", {}, h("dt", {}, "Exception"), h("dd", {}, `${c.staff.message} ${c.staff.next}`)) : null,
      c.result && c.result.code ? h("div", {}, h("dt", {}, "Reason"), h("dd", {}, c.reason ? c.reason.message : c.staff ? c.staff.message : c.result.code)) : null,
      h("div", {}, h("dt", {}, "Opened"), h("dd", {}, when(c.started_at)))));
  const settled = !c.can_review && c.needs !== "review" && c.phase !== "waiting-for-review" && !c.resolve_kinds.length;
  if (S.follow && S.follow.id === id && settled) S.follow = null;
  const following = Boolean(S.follow && S.follow.id === id);
  const nodes = [h("p", {}, h("a", { href: "#/cases" }, "← All cases"))];
  if (following) nodes.push(h("p", { class: "notice", role: "status" }, "Your decision was recorded. The case is moving on…"));
  if (c.can_review) {
    nodes.push(h("section", { class: "panel" }, h("strong", {}, "Your decision"),
      h("p", {}, `Approve or reject a refund of ${money(p.amount_minor, p.currency)} for booking ${p.booking_id}.`,
        c.phase === "waiting-for-consent" ? " The customer has not confirmed yet. Your review is kept, and the refund waits for the customer." : ""),
      h("div", { class: "row" },
        h("button", { class: "btn primary", type: "button", onclick: () => confirmPanel("Approve this refund?", `Refund ${money(p.amount_minor, p.currency)} to the customer's ${p.payment_kind} (digest ${short(p.digest)}). This cannot be undone once the refund is issued.`, "Approve refund", () => decide(id, "review", { verdict: "approved", digest: p.digest })) }, "Approve…"),
        h("button", { class: "btn danger", type: "button", onclick: () => confirmPanel("Reject this refund?", "Nothing will be written. The customer has to ask again for a new quote.", "Reject", () => decide(id, "review", { verdict: "rejected", digest: p.digest }), true) }, "Reject…"))));
  }
  if (c.resolve_kinds.length) {
    nodes.push(h("section", { class: "panel" }, h("strong", {}, "Resolve this exception"), h("p", {}, c.staff ? c.staff.next : ""),
      h("div", { class: "row" }, c.resolve_kinds.map((kind) => h("button", { class: kind === "close" ? "btn" : "btn primary", type: "button",
        onclick: () => confirmPanel(kind === "close" ? "Close this exception?" : "Complete the refund?", kind === "close" ? "The case ends without a refund. Do this only when nothing is owed." : "A supervisor completes the refund after a recheck. There is no rollback.", kind === "close" ? "Close exception" : "Complete refund", () => decide(id, "resolve", { kind }), kind === "close") }, kind === "close" ? "Close exception…" : "Complete refund…")))));
  }
  const roles = S.me ? S.me.roles.join(", ") : "";
  if (c.needs === "staff" && !c.resolve_kinds.length) nodes.push(h("p", { class: "notice info", role: "note" }, `Resolving an exception needs the operator role. You are signed in as ${roles}.`));
  if (c.needs === "review" && !c.can_review) nodes.push(h("p", { class: "notice info", role: "note" }, `Reviewing a refund needs the reviewer role, and not the customer's own. You are signed in as ${roles}.`));
  nodes.push(head);
  nodes.push(h("section", { class: "card" }, h("h2", {}, "What happened"),
    c.timeline.length ? h("ol", { class: "timeline" }, c.timeline.map((e) => h("li", {}, h("time", { datetime: e.at, title: e.at }, new Date(e.at).toLocaleTimeString()), h("span", { class: `mark ${e.state}`, "aria-label": e.state === "ok" ? "done" : e.state === "warn" ? "note" : "failed" }, e.state === "ok" ? "✓" : e.state === "warn" ? "!" : "×"), h("div", {}, e.label, e.detail ? h("small", {}, e.detail) : null)))) : empty("No events yet")));
  const ev = c.evidence;
  nodes.push(h("section", { class: "card" }, h("h2", {}, "Model calls and decision"),
    !ev ? empty("No audit log is connected", "Start the cockpit with --audit-log to see the model calls and decisions of this case.") :
    !c.correlation ? empty("No correlation id", "Cases opened with `orchestration ask` carry one, and that is how the calls are matched.") :
    h("div", {},
      h("p", { class: "note" }, `${ev.calls.length} model call${ev.calls.length === 1 ? "" : "s"} · ${ev.decisions.length} decision${ev.decisions.length === 1 ? "" : "s"} · correlation `, h("code", {}, c.correlation)),
      ev.calls.length ? callsTable(ev.calls) : null,
      ev.decisions.map((d) => decisionBlock(id, d)))));
  if (following) {
    if (S.follow.left-- <= 0) S.follow = null;
    else setTimeout(() => { if (!S.busy && route().id === id) render(); }, 1500);
  }
  return h("div", {}, nodes);
}

function callsTable(calls) {
  const rows = calls.map((call) => h("tr", {},
    cell("Time", h("time", { datetime: call.at, title: call.at }, new Date(call.at).toLocaleTimeString())),
    cell("Model", call.model || "—"), cell("Tools asked for", call.tools.length ? call.tools.join(", ") : "none"),
    cell("Tokens", call.tokens_in === null || call.tokens_in === undefined ? "unknown" : `${call.tokens_in} in / ${call.tokens_out} out`),
    cell("Inspection", call.action || "—", call.findings ? ` (${call.findings} finding${call.findings === 1 ? "" : "s"})` : ""),
    cell("Status", call.status === null || call.status === undefined ? "—" : String(call.status)),
    h("td", { class: "actions", "data-label": "Console" }, call.link ? h("a", { href: call.link, target: "_blank", rel: "noopener noreferrer", "aria-label": `Open model call ${call.id.slice(0, 8)} in the audit console` }, "Open") : "")));
  return table("Model calls table", ["Time", "Model", "Tools asked for", "Tokens", "Inspection", "Status", ""], rows);
}

async function download(caseId, decisionId, format) {
  const response = await fetch(`/api/cases/${encodeURIComponent(caseId)}/receipt`, {
    method: "POST", headers: { Authorization: `Bearer ${S.token}`, "Content-Type": "application/json" }, body: JSON.stringify({ decision: decisionId, format }),
  });
  if (!response.ok) { const data = await response.json().catch(() => ({})); throw Object.assign(new Error(data.message), { data }); }
  const name = /filename="([^"]+)"/.exec(response.headers.get("Content-Disposition") || "")?.[1] || `decision.${format}`;
  const link = h("a", { href: URL.createObjectURL(await response.blob()), download: name });
  document.body.append(link); link.click(); link.remove();
}

function decisionBlock(caseId, d) {
  const result = h("p", { class: "note", role: "status" }, S.replays[d.id] || "");
  const action = (work) => (event) => working(event.currentTarget, async () => { try { clearError(); await work(); } catch (error) { showError(error); } });
  return h("div", { class: "decision" },
    h("dl", { class: "kv" },
      h("dt", {}, "Decision"), h("dd", { class: "mono" }, d.id),
      h("dt", {}, "Reviews"), h("dd", {}, d.reviews.length ? d.reviews.map((r) => `${r.reviewer}: ${r.verdict}`).join("; ") : "None yet"),
      h("dt", {}, "Outcome"), h("dd", {}, d.effects.length ? d.effects.map((x) => `${x.action}: ${x.status}`).join("; ") : "Not recorded")),
    h("div", { class: "row" },
      d.can_replay ? h("button", { class: "btn", type: "button", onclick: action(async () => {
        const r = await api(`/api/cases/${encodeURIComponent(caseId)}/replay`, { body: { decision: d.id } });
        S.replays[d.id] = `${r.reproduced ? "Reproduced" : "NOT reproduced"} from the log alone. Chain ${r.chain_ok ? "intact" : "BROKEN"}. Anchor: ${r.anchor || "none"}. Result: ${JSON.stringify(r.outputs).slice(0, 240)}`;
        result.textContent = S.replays[d.id];
      }) }, "Replay from the log") : null,
      d.can_replay ? ["svg", "json", "html"].map((format) => h("button", { class: "btn", type: "button", onclick: action(() => download(caseId, d.id, format)) }, { svg: "Receipt (SVG)", json: "Receipt (JSON)", html: "Full receipt (HTML)" }[format])) : null,
      d.link ? h("a", { class: "btn", href: d.link, target: "_blank", rel: "noopener noreferrer" }, "Open in the audit console") : null),
    result,
    d.can_replay ? h("p", { class: "note" }, "SVG and JSON carry the exact rule and inputs but no reviewer names. The full HTML carries reviewer names and notes. Read what you share. Whoever gets an SVG can run `ai-audit verify-receipt`; it proves the calculation, not who approved it.") : h("p", { class: "note" }, `Replay and receipts need --audit-cli. By hand: ${d.receipt}`));
}

const TIER_HELP = { shadow: "drafts beside people, nothing is delivered", assist: "suggests, a person delivers", contain: "answers within scope", transact: "may write refunds up to the limit" };

async function authorityPage() {
  const a = await api("/api/authority");
  const tones = { active: "ok", approved: "info", replaced: "unknown", expired: "bad", revoked: "bad" };
  const form = grantForm(a);
  const rows = a.grants.map((g) => h("tr", {},
    cell("Operation", h("strong", {}, g.intent), h("div", { class: "note" }, `${g.actions.join(", ")}. Approved by ${g.approved_by}${g.activated_by ? `, activated by ${g.activated_by}` : ""}${g.revoke_reason ? `. Revoked: ${g.revoke_reason}` : ""}`)),
    cell("State", pill(tones[g.state] || "unknown", g.state)), cell("Tier", g.tier),
    h("td", { class: "amount", "data-label": "Limit" }, `up to ${money(g.max_amount_minor, g.currency)}`),
    h("td", { class: "nowrap", "data-label": "Expires" }, when(g.expires_at)),
    h("td", { class: "actions", "data-label": "" }, h("div", { class: "btn-row" },
      a.can_activate && g.state === "approved" ? h("button", { class: "btn small primary", type: "button", "aria-label": `Activate the ${g.tier} grant for ${g.intent}`, onclick: () => confirmPanel("Activate this grant?", `The ${g.tier} grant for ${g.intent}, up to ${money(g.max_amount_minor, g.currency)}, replaces the active one. ${g.tier === "transact" ? "From now on the desk may write refunds within this limit. Nothing has been qualified." : ""}`, "Activate", () => api(`/api/authority/${g.id}/activate`, { body: {} })) }, "Activate…") : null,
      a.can_approve && (g.state === "active" || g.state === "approved") ? h("button", { class: "btn small", type: "button", "aria-label": `Replace the grant for ${g.intent} with a new one`, onclick: () => form.prefill(g) }, "Replace…") : null,
      a.can_revoke && (g.state === "active" || g.state === "approved") ? h("button", { class: "btn small danger", type: "button", "aria-label": `Revoke the grant for ${g.intent}`, onclick: () => {
        const reason = h("input", { id: "reason", type: "text", maxlength: "300", required: true, placeholder: "Why are you revoking it?", "aria-label": "Reason for revoking" });
        confirmPanel("Revoke this grant?", "The operation falls back to a person until a new grant is activated.", "Revoke", async () => { if (!reason.value.trim()) throw Object.assign(new Error("A reason is needed."), { data: { message: "A reason is needed.", next: "Say why you are revoking the grant." } }); await api(`/api/authority/${g.id}/revoke`, { body: { reason: reason.value } }); }, true);
        document.querySelector(".panel p").after(reason); reason.focus();
      } }, "Revoke…") : null))));
  return h("div", {}, h("section", { class: "card" }, h("h2", {}, `${a.grants.length} grant${a.grants.length === 1 ? "" : "s"}`),
    h("p", {}, "A grant lets one operation run without a person up to a limit. Approving and activating are two steps, by roles that can be two people. Only a transact grant lets the desk write."),
    a.grants.length ? table("Grants table", ["Operation", "State", "Tier", "Limit*", "Expires", ""], rows) : empty("No grant exists", "Every cancellation waits for a person.")),
    form.node);
}

function grantForm(a) {
  if (!a.can_approve) return { node: h("section", { class: "card" }, h("h2", {}, "Approve a grant"), empty("Your role cannot approve grants", "A reviewer can.")), prefill() {} };
  const intent = h("select", { id: "g-intent" }, a.intents.map((name) => h("option", { value: name }, name)));
  const tier = h("select", { id: "g-tier", "aria-describedby": "g-tier-help" }, a.tiers.map((name) => h("option", { value: name }, name)));
  const tierHelp = h("p", { class: "note", id: "g-tier-help" });
  const limit = h("input", { id: "g-limit", type: "number", min: "1", max: String(a.max_amount_minor / 100), step: "1", value: "1000", required: true });
  const days = h("input", { id: "g-days", type: "number", min: "1", max: "365", step: "1", value: "90", required: true });
  const ack = h("input", { id: "g-ack", type: "checkbox" });
  const warn = h("p", { class: "note", role: "status" });
  const sync = () => {
    tierHelp.textContent = `${tier.value}: ${TIER_HELP[tier.value]}.`;
    const live = tier.value === "transact";
    ack.disabled = !live; ack.closest("label").hidden = !live;
    warn.textContent = live ? "Nothing has been qualified: no human-scored evidence exists. A transact grant lets the desk write refunds up to the limit without a person for the smaller ones." : "";
  };
  tier.addEventListener("change", sync);
  const submit = h("button", { class: "btn primary", type: "submit" }, "Approve grant");
  const node = h("section", { class: "card", id: "grant-form" }, h("h2", {}, "Approve a grant"),
    h("p", {}, `Bound to release ${a.release}. It is approved here and activated as a second step. A change of the refund rule or policy text makes it unusable until a new one is activated.`),
    h("form", { onsubmit: (event) => {
      event.preventDefault();
      working(submit, async () => {
        try {
          await api("/api/authority", { body: { intent: intent.value, tier: tier.value, max_amount_minor: Math.round(Number(limit.value) * 100), days: Number(days.value), acknowledged: tier.value === "transact" ? ack.checked : false } });
          clearError(); await render();
        } catch (error) { showError(error); }
      });
    } },
      h("div", { class: "fields" },
        h("label", { for: "g-intent" }, "Operation", intent), h("label", { for: "g-tier" }, "Tier", tier),
        h("label", { for: "g-limit" }, "Limit per refund (EUR)", limit), h("label", { for: "g-days" }, "Valid for (days)", days)),
      tierHelp,
      h("label", { class: "check", hidden: true }, ack, h("span", {}, "I know nothing has been qualified and I accept that.")), warn,
      h("div", { class: "row" }, submit)));
  sync();
  return { node, prefill(g) { tier.value = g.tier; limit.value = String(g.max_amount_minor / 100); sync(); node.scrollIntoView({ behavior: "smooth" }); tier.focus(); } };
}

async function rulesPage() {
  const { sections, note } = await api("/api/rules");
  return h("div", {}, sections.map((section) => h("section", { class: "card" }, h("h2", {}, section.title),
    h("dl", { class: "kv" }, section.rows.map((row) => [h("dt", {}, row.label), h("dd", {}, h("strong", {}, row.value), row.note ? h("div", { class: "note" }, row.note) : null)])))), h("p", { class: "note" }, note));
}

async function conversationsPage() {
  const { configured, conversations } = await api("/api/conversations");
  if (!configured) return h("section", { class: "card" }, h("h2", {}, "No audit log"), empty("Nothing to show", "Start the cockpit with --audit-log to see the questions that did not open a case."));
  const rows = conversations.map((c) => h("tr", {},
    cell("Last call", when(c.last_at)), cell("Calls", String(c.calls)), cell("Model", c.models.join(", ") || "—"),
    cell("Tools asked for", c.tools.length ? c.tools.join(", ") : "none"), cell("Inspection", c.actions.join(", ") || "—", c.errors ? ` · ${c.errors} failed` : ""),
    cell("Tokens", `${c.tokens_in} in / ${c.tokens_out} out`),
    cell("Correlation", c.correlation ? h("code", {}, c.correlation.slice(0, 18)) : h("em", {}, "no correlation id")),
    h("td", { class: "actions", "data-label": "Console" }, c.link ? h("a", { href: c.link, target: "_blank", rel: "noopener noreferrer", "aria-label": "Open the first call of this conversation in the audit console" }, "Open") : "")));
  return h("section", { class: "card" }, h("h2", {}, `${conversations.length} conversation${conversations.length === 1 ? "" : "s"} without a case`),
    h("p", {}, "Questions that were answered, refused or held and never opened a cancellation case. The log keeps no customer text, so only the shape shows: when, how many model calls, which tools were asked for and what the inspection did."),
    conversations.length ? table("Conversations table", ["Last call", "Calls", "Model", "Tools asked for", "Inspection", "Tokens", "Correlation", ""], rows) : empty("Every conversation opened a case"));
}

async function qualityPage() {
  const { configured, runs } = await api("/api/quality");
  if (!configured) return h("section", { class: "card" }, h("h2", {}, "No evaluation folder"), empty("Nothing to show", "Start the cockpit with --evals-dir pointing at the IntentGauge runs folder."));
  const rows = runs.map((r) => h("tr", {},
    h("td", { class: "nowrap", "data-label": "When" }, when(r.created_at)), h("td", { class: "nowrap", "data-label": "Model" }, r.mode),
    cell("Trials passed", `${r.passed_trials} of ${r.trials}`, h("div", { class: "note" }, `${r.cases} case${r.cases === 1 ? "" : "s"} × ${r.repetitions}`)),
    h("td", { class: "amount", "data-label": "Lower bound" }, r.pass_rate_lower_bound === null || r.pass_rate_lower_bound === undefined ? "—" : `${Math.round(r.pass_rate_lower_bound * 100)}%`),
    cell("Critical", r.critical_failures ? pill("bad", `${r.critical_failures} critical`) : pill("ok", "none")),
    cell("Needs a look", r.flaky_cases.length || r.failing_cases.length ? [r.failing_cases.length ? h("div", {}, h("strong", {}, "Failing: "), r.failing_cases.join(", ")) : null, r.flaky_cases.length ? h("div", {}, h("strong", {}, "Flaky: "), r.flaky_cases.join(", ")) : null] : "—"),
    h("td", { class: "actions", "data-label": "" }, h("div", { class: "btn-row" }, h("button", { class: "btn small", type: "button", "aria-pressed": String(S.run === r.run), onclick: () => { S.run = S.run === r.run ? null : r.run; render().then(() => { const box = document.getElementById("run-detail"); if (box) box.scrollIntoView({ behavior: "smooth", block: "start" }); }); } }, S.run === r.run ? "Hide cases" : "Cases"),
      r.dashboard ? h("a", { class: "btn small", href: `/evals/${encodeURIComponent(r.run)}/${r.dashboard}`, target: "_blank", rel: "noopener noreferrer" }, "Full report") : null))));
  const detail = S.run ? (await api(`/api/quality/${encodeURIComponent(S.run)}`)).cases : null;
  return h("div", {}, h("div", { class: "banner warn", role: "note" }, "Not qualified. These runs use development cases and no human-scored evidence. A passing run shows the checks held; it does not show the agent is ready to act alone."),
    h("section", { class: "card" }, h("h2", {}, "Latest runs"),
      runs.length ? table("Evaluation runs table", ["When", "Model", "Trials passed", "Lower bound*", "Critical", "Needs a look", ""], rows) : empty("No evaluation run", "None was found for this application.")),
    detail ? h("section", { class: "card", id: "run-detail" }, h("h2", {}, `Cases in ${S.run}`),
      table("Cases of the run", ["Case", "Passed", "What failed"], detail.map((c) => h("tr", {}, cell("Case", c.case), cell("Passed", pill(c.passed === c.trials ? "ok" : c.passed === 0 ? "bad" : "warn", `${c.passed} of ${c.trials}`)),
        cell("What failed", Object.keys(c.failed_checks).length ? Object.entries(c.failed_checks).map(([name, reason]) => h("div", {}, h("strong", {}, name), ": ", reason)) : "—"))))) : null);
}

async function healthPage() {
  const { overall, checks } = await api("/api/health");
  const words = { ok: "OK", warn: "Warning", bad: "Problem", unknown: "Unknown" };
  return h("div", {}, h("div", { class: `banner ${overall === "unknown" ? "warn" : overall}`, role: "status" }, overall === "ok" ? "Everything is working" : overall === "bad" ? "Something needs fixing" : "Check the warnings"),
    h("div", { class: "status-grid" }, checks.map((check) => h("div", { class: `status-card ${check.state}` }, h("div", {}, pill(check.state, words[check.state])), h("strong", {}, check.label), h("span", {}, check.detail)))));
}

document.getElementById("refresh").addEventListener("click", () => { clearError(); render(); });
document.getElementById("auto").addEventListener("change", schedule);
document.getElementById("theme").addEventListener("click", cycleTheme);
document.getElementById("signout").addEventListener("click", () => { fetch("/api/signout", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).catch(() => {}); signOut(); });
function takeToken() {
  const match = /(?:^#|[?&])token=([A-Za-z0-9_-]+)/.exec(location.hash);
  if (!match) return null;
  const rest = location.hash.replace(match[0], "").replace(/^[&?]/, "").replace(/^#/, "");
  history.replaceState(null, "", location.pathname + location.search + (rest.startsWith("/") ? `#${rest}` : ""));
  return match[1];
}

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
  const given = takeToken();
  if (given) await adopt(given);
  render().then(() => { window.scrollTo(0, 0); document.getElementById("main").focus({ preventScroll: true }); });
});
function schedule() {
  clearInterval(S.timer);
  if (document.getElementById("auto").checked) S.timer = setInterval(() => { if (!S.busy && S.token) render(); }, 15000);
}

(async function start() {
  S.theme = loadTheme();
  applyTheme();
  const given = takeToken();
  const token = given || sessionStorage.getItem("cockpit-token");
  if (token) await adopt(token);
  schedule();
  render();
})();
