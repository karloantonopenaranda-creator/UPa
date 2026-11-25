"use strict";
/* DormCare Live Alerts mailroom.
   Everything on this page comes from the dashboard server's event stream (GET /events), which reports what
   the real broker and subscriber processes print, plus the broker's STATUS once a second. */

const $ = (sel, root = document) => root.querySelector(sel);
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

const state = {
  scenario: null,
  processes: {},     // process name -> running?
  status: null,      // latest broker STATUS: { clients: [...], publishers_online: [...] }
  lastPending: {},   // client id -> queued messages (kept on screen while the broker is down)
  notes: {},         // message key -> { el, lines }
  connection: {},    // subscriber id -> "connecting" | "connected" | "offline", from its own output
  liveSince: 0,      // events older than this are a replay after (re)connecting: no animation
  connected: false,
};

const DESK_NAMES = { "manager-office": "Manager's office", "maintenance-desk": "Maintenance desk" };
const LABELS = { LIVE: "Live", "CATCH-UP": "Caught up", REDELIVERY: "Redelivered", DUPLICATE: "Duplicate ignored" };
const MINOR_TAGS = new Set(["DELIVER", "ACK"]);
const MAX_LOG = 300;
const MAX_NOTES = 40;

// --- small helpers ---------------------------------------------------------------------------------
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === false || value == null) continue;
    if (key === "class") node.className = value;
    else if (key === "style") node.style.cssText = value;
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const propertyOf = (topic) => (topic || "").split(".")[0];
const propColor = (topic) => {
  const p = propertyOf(topic);
  return p === "casa-jaro" ? "var(--casa)" : p === "molo-hub" ? "var(--molo)" : p === "dormcare" ? "var(--dormcare)" : "var(--line)";
};
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const byId = (selector, id) => document.querySelector(`${selector}[data-id="${CSS.escape(id)}"]`);

function toast(message, kind = "") {
  const t = $("#toast");
  t.textContent = message;
  t.className = `toast show ${kind}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.className = "toast"; }, kind === "error" ? 6000 : 3200);
}

async function api(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}) });
  let data = {};
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok || data.ok === false) throw new Error(data.error || `The dashboard server answered ${res.status}.`);
  return data;
}

async function act(button, work) {
  if (button) button.disabled = true;
  try { await work(); }
  catch (err) { toast(err.message, "error"); }
  finally { if (button) button.disabled = false; render(); }
}

function subscriberSpec(id) { return state.scenario.subscribers.find((s) => s.id === id); }
function splitName(text) {
  const i = (text || "").indexOf(",");
  return i < 0 ? [text, ""] : [text.slice(0, i), text.slice(i + 1).trim()];
}
function initials(name) {
  return name.split(/\s+/).filter((w) => /^[A-Za-z]/.test(w)).map((w) => w[0].toUpperCase()).slice(0, 2).join("");
}
function topicMatches(pattern, topic) {
  return pattern === topic || (pattern.startsWith("*.") && topic.endsWith(pattern.slice(1)));
}

// --- building the page ---------------------------------------------------------------------------------
function buildDesks() {
  const root = $("#desks");
  const allTopics = Object.keys(state.scenario.topics);
  for (const pub of state.scenario.publishers) {
    const allowed = allTopics.filter((t) => (pub.publishes || []).some((p) => topicMatches(p, t)));
    const result = el("p", { class: "desk-result", "aria-live": "polite" }, "Pick a notice to publish it.");
    const list = el("ul", { class: "notices" }, pub.events.map((ev) => {
      const button = el("button", { type: "button", class: "notice", style: `--prop:${propColor(ev.topic)}`,
        title: ev.body }, el("span", { class: "topic" }, ev.topic), el("span", { class: "title" }, ev.title));
      button.addEventListener("click", () => act(button, async () => {
        showDeskResult(pub.id, "Publishing…", "");
        await api("/api/publish", { publisher: pub.id, event: ev.index });
      }));
      return el("li", {}, button);
    }));
    const topicSelect = el("select", { name: "topic" }, allowed.map((t) => el("option", { value: t }, t)));
    const titleInput = el("input", { name: "title", required: true, maxlength: 120, placeholder: "e.g. Generator test on Sunday" });
    const bodyInput = el("textarea", { name: "body", maxlength: 400, placeholder: "Details for tenants (optional)" });
    const send = el("button", { type: "submit", class: "btn" }, "Publish");
    const form = el("form", {},
      el("label", {}, "Topic", topicSelect), el("label", {}, "Title", titleInput), el("label", {}, "Message", bodyInput), send);
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      act(send, async () => {
        showDeskResult(pub.id, "Publishing…", "");
        await api("/api/publish", { publisher: pub.id, topic: topicSelect.value, title: titleInput.value, body: bodyInput.value });
        titleInput.value = ""; bodyInput.value = "";
      });
    });
    root.append(el("article", { class: "desk", "data-id": pub.id },
      el("header", { class: "desk-head" }, el("h3", {}, DESK_NAMES[pub.id] || pub.id), el("p", {}, pub.name || "")),
      list,
      el("details", { class: "compose" }, el("summary", {}, "Write your own notice"), form),
      result));
  }
}

function buildWall() {
  const wall = $("#wall");
  for (const sub of state.scenario.subscribers) {
    wall.append(el("div", { class: "slot", "data-id": sub.id },
      el("div", { class: "slot-label" }, el("span", { class: "lamp" }), el("b", {}, sub.id), el("span", { class: "slot-state" }, "")),
      el("div", { class: "slot-box" }, el("div", { class: "stack" }), el("span", { class: "count" }, ""))));
  }
}

function buildPhones() {
  const root = $("#phones");
  for (const sub of state.scenario.subscribers) {
    const [name, role] = splitName(sub.name || sub.id);
    const color = propColor(sub.topics[0]);
    const crash = el("button", { type: "button", class: "btn btn-crash" }, "Crash");
    const start = el("button", { type: "button", class: "btn btn-start" }, "Start");
    crash.addEventListener("click", () => act(crash, () => api(`/api/subscribers/${sub.id}/crash`)));
    start.addEventListener("click", () => act(start, () => api(`/api/subscribers/${sub.id}/start`)));
    root.append(el("article", { class: "phone is-off", "data-id": sub.id, style: `--prop:${color}` },
      el("div", { class: "phone-body" },
        el("div", { class: "screen" },
          el("div", { class: "status-bar" }, el("span", { class: "lamp" }), el("span", { class: "state-text" }, "Off"),
            el("span", { class: "clock" }, "")),
          el("header", { class: "phone-head" }, el("span", { class: "avatar" }, initials(name)),
            el("div", {}, el("h3", {}, name), el("p", {}, role ? `${role} (${sub.id})` : sub.id))),
          el("ul", { class: "topics" }, sub.topics.map((t) => el("li", { style: `--prop:${propColor(t)}` }, t))),
          el("p", { class: "waiting-note", hidden: true }, ""),
          el("ol", { class: "inbox" }, el("li", { class: "empty" }, "No notices yet.")),
          el("div", { class: "screen-off" }, el("b", {}, "Off"),
            el("span", {}, `Subscriber process stopped. Messages for ${sub.id} wait at the broker. Press Start to reconnect.`)))),
      el("div", { class: "phone-controls" }, crash, start)));
  }
}

function wireHeader() {
  const demo = $("#btn-demo");
  demo.addEventListener("click", () => act(demo, () => api("/api/demo/start")));
  const broker = $("#btn-broker");
  broker.addEventListener("click", () => act(broker, () =>
    api(state.processes.broker ? "/api/broker/crash" : "/api/broker/start")));
  const reset = $("#btn-reset");
  reset.addEventListener("click", () => {
    if (!confirm("Reset the demo? This stops the broker and every subscriber and deletes all queued messages.")) return;
    act(reset, () => api("/api/reset"));
  });
  document.querySelectorAll("[data-fault]").forEach((item) => item.addEventListener("click", () => {
    item.closest("details").open = false;
    act(item, async () => {
      if (item.dataset.fault === "crash-before-ack") {
        await api("/api/subscribers/tenant-juan/crash");
        await api("/api/subscribers/tenant-juan/start", { crash_after: 1 });
        toast("Juan will crash right after his next notice, before he acknowledges it. Publish a Casa Jaro notice.");
      } else {
        await api("/api/broker/crash");
        await sleep(400);
        await api("/api/broker/start", { fault_skip_puback: 1 });
        toast("The broker will drop the confirmation for the next notice. Publish one and watch the desk.");
      }
    });
  }));
  $("#show-minor").addEventListener("change", (e) => $("#log").classList.toggle("show-minor", e.target.checked));
}

// --- rendering state ---------------------------------------------------------------------------------
function clientStatus(id) { return state.status?.clients?.find((c) => c.client_id === id); }

function render() {
  if (!state.scenario) return;
  const brokerUp = !!state.processes.broker;
  const pill = $("#broker-state");
  pill.className = `broker-state ${brokerUp ? "is-up" : "is-down"}`;
  pill.lastElementChild.textContent = brokerUp ? `Broker running on port ${state.scenario.broker_port}` : "Broker stopped";
  const brokerBtn = $("#btn-broker");
  brokerBtn.textContent = brokerUp ? "Crash broker" : "Start broker";
  brokerBtn.classList.toggle("is-danger", brokerUp);

  $("#wall").classList.toggle("is-down", !brokerUp);
  $("#wall-caption").hidden = brokerUp || !Object.values(state.lastPending).some((n) => n > 0);

  for (const sub of state.scenario.subscribers) {
    renderSlot(sub.id, brokerUp);
    renderPhone(sub.id, brokerUp);
  }
}

function renderSlot(id, brokerUp) {
  const slot = byId(".slot", id);
  const client = clientStatus(id);
  if (client) state.lastPending[id] = client.pending;
  const pending = state.lastPending[id] || 0;
  const online = !!client?.online;
  slot.classList.toggle("is-online", brokerUp && online);
  slot.classList.toggle("is-offline", brokerUp && !!client && !online);
  slot.classList.toggle("has-mail", pending > 0);
  slot.querySelector(".slot-state").textContent = !brokerUp ? "broker down" : !client ? "not subscribed yet" : online ? "online" : "offline";
  const count = slot.querySelector(".count");
  if (pending) count.replaceChildren(el("b", {}, String(pending)), "waiting");
  else count.replaceChildren("Empty");
  count.setAttribute("aria-label", pending ? `${plural(pending, "message")} waiting` : "Empty");
  const stack = slot.querySelector(".stack");
  const shown = Math.min(pending, 8);
  if (stack.childElementCount !== shown) {
    stack.replaceChildren(...Array.from({ length: shown }, (_, i) =>
      el("span", { class: "envelope", style: `bottom:${i * 5}px; left:${(i % 2) * 6}px` })));
  }
}

function renderPhone(id, brokerUp) {
  const phone = byId(".phone", id);
  const running = !!state.processes[id];
  // The subscriber's own output (CONNECTED / OFFLINE) is instant; the broker's STATUS is the fallback.
  const conn = state.connection[id];
  const online = conn ? conn === "connected" : !!clientStatus(id)?.online;
  let key, text;
  if (!running) { key = "off"; text = "Off"; }
  else if (brokerUp && online) { key = "online"; text = "Online"; }
  else { key = "waiting"; text = brokerUp ? "Connecting" : "Broker down, retrying"; }
  phone.classList.toggle("is-off", key === "off");
  phone.classList.toggle("is-online", key === "online");
  phone.classList.toggle("is-waiting", key === "waiting");
  phone.querySelector(".state-text").textContent = text;
  const [crash, start] = phone.querySelectorAll(".phone-controls .btn");
  crash.disabled = !running;
  start.disabled = running;
  const pending = state.lastPending[id] || 0;
  const note = phone.querySelector(".waiting-note");
  note.hidden = !(pending > 0 && key !== "online");
  note.textContent = `${plural(pending, "message")} waiting for this phone at the broker`;
  phone.querySelector(".screen-off span").textContent = pending > 0
    ? `Subscriber process stopped. ${plural(pending, "message")} waiting at the broker. Press Start to reconnect and catch up.`
    : `Subscriber process stopped. New messages for ${id} will wait at the broker. Press Start to reconnect.`;
}

function tickClocks() {
  const now = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  document.querySelectorAll(".status-bar .clock").forEach((c) => { c.textContent = now; });
}

// --- events from the server --------------------------------------------------------------------------
function resetDynamic() {
  document.querySelectorAll(".inbox").forEach((inbox) => inbox.replaceChildren(el("li", { class: "empty" }, "No notices yet.")));
  $("#log").replaceChildren(el("li", { class: "empty-log" }, "Start the demo to see the broker's decisions here."));
  state.notes = {};
}

function handle(ev) {
  switch (ev.type) {
    case "snapshot":
      resetDynamic();
      state.liveSince = Date.now() / 1000;
      state.processes = { ...ev.processes };
      state.status = ev.status;
      setBanner(ev.external_broker
        ? `A broker is already running on port ${ev.broker_port}, probably the command-line demo. Close those windows before pressing Start demo.`
        : "");
      break;
    case "process":
      state.processes[ev.name] = ev.state === "running";
      if (ev.name === "broker") {
        if (ev.state === "running") setBanner("");
        else for (const id of Object.keys(state.connection)) state.connection[id] = "offline";
      } else {
        state.connection[ev.name] = ev.state === "running" ? "connecting" : "offline";
      }
      break;
    case "sub_state":
      state.connection[ev.subscriber] = ev.tag === "CONNECTED" ? "connected" : "offline";
      break;
    case "status":
      state.status = ev.status;
      break;
    case "log":
      if (ev.source === "broker") addLog(ev);
      else if (byId(".desk", ev.source) && ev.tag === "RETRY") showDeskResult(ev.source, `Retrying: ${ev.text}`, "warn");
      return;
    case "message": addNote(ev); return;
    case "message_line": fillNote(ev); return;
    case "route": if (ev.at >= state.liveSince) animateRoute(ev); return;
    case "puback": deskPuback(ev); return;
    case "reset":
      resetDynamic();
      state.status = null;
      state.lastPending = {};
      state.processes = {};
      toast("Demo reset. The broker's queues and the subscriber logs were deleted.");
      break;
    default: return;
  }
  render();
}

function addLog(ev) {
  const log = $("#log");
  log.querySelector(".empty-log")?.remove();
  const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  const cont = !ev.tag;
  const li = cont
    ? el("li", { class: "cont" }, el("span", { class: "text" }, ev.text))
    : el("li", { class: MINOR_TAGS.has(ev.tag) ? "minor" : "" },
        el("time", {}, ev.time), el("span", { class: `tag t-${ev.tag}` }, ev.tag), el("span", { class: "text" }, ev.text));
  if (cont && log.lastElementChild?.classList.contains("minor")) li.classList.add("minor");
  log.append(li);
  while (log.childElementCount > MAX_LOG) log.firstElementChild.remove();
  if (nearBottom) log.scrollTop = log.scrollHeight;
}

function addNote(ev) {
  const phone = byId(".phone", ev.subscriber);
  if (!phone) return;
  const label = ev.label;
  const head = el("div", { class: "note-head" }, el("span", { class: `chip chip-${label}` }, LABELS[label] || label),
    ev.seq != null ? el("span", {}, `#${ev.seq}`) : null,
    label === "CATCH-UP" && ev.queued_s != null ? el("span", {}, `waited ${ev.queued_s} s`) : null,
    el("time", {}, ev.time));
  const note = el("li", { class: `note ${label}`, style: `--prop:${propColor(ev.topic)}` }, head);
  if (label === "DUPLICATE") {
    note.append(el("p", { class: "note-body" }, `Message #${ev.seq} was already processed before the crash, so it was not shown again. The phone re-sent its ACK.`));
  } else {
    note.append(el("p", { class: "note-meta" }, `${ev.topic} from ${ev.publisher}`));
  }
  state.notes[ev.key] = { el: note, lines: 0 };
  const insert = () => {
    const inbox = phone.querySelector(".inbox");
    inbox.querySelector(".empty")?.remove();
    note.classList.add("arriving");
    inbox.prepend(note);
    while (inbox.childElementCount > MAX_NOTES) inbox.lastElementChild.remove();
  };
  // A live message is shown when its envelope lands on the phone (after the flight animation).
  const isLive = label === "LIVE" && ev.at >= state.liveSince && !reduceMotion.matches;
  if (isLive) setTimeout(insert, 1150); else insert();
}

function fillNote(ev) {
  const entry = state.notes[ev.key];
  if (!entry) return;
  const meta = entry.el.querySelector(".note-meta");
  const p = el("p", { class: entry.lines === 0 ? "note-title" : "note-body" }, ev.text);
  entry.el.insertBefore(p, meta);
  entry.lines += 1;
}

function showDeskResult(publisherId, text, kind) {
  const desk = byId(".desk", publisherId);
  if (!desk) return;
  const p = desk.querySelector(".desk-result");
  p.textContent = text;
  p.className = `desk-result ${kind}`;
}

function deskPuback(ev) {
  if (!ev.ok) { showDeskResult(ev.publisher, ev.error, "error"); return; }
  let text;
  if (ev.duplicate) text = `Published #${ev.seq}. The first confirmation was lost, so the desk re-sent it; the broker recognised the duplicate and did not deliver it twice.`;
  else if (ev.routed_to === 0) text = `Published #${ev.seq}. Nobody subscribes to ${ev.topic}, so the broker only logged it.`;
  else text = `Published #${ev.seq} to ${plural(ev.routed_to, "subscriber")} of ${ev.topic}.`;
  showDeskResult(ev.publisher, text, ev.duplicate ? "warn" : "ok");
}

// --- the one animation: an envelope travels desk -> pigeonhole -> phone ----------------------------------
function centre(node) {
  const r = node.getBoundingClientRect();
  return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
}

function fly(from, to) {
  return new Promise((resolve) => {
    if (!from || !to || reduceMotion.matches) { resolve(); return; }
    const a = centre(from), b = centre(to);
    const envelope = el("div", { class: "flight", "aria-hidden": "true" });
    document.body.append(envelope);
    const animation = envelope.animate([
      { transform: `translate(${a.x}px, ${a.y}px) translate(-50%, -50%) scale(.85)`, opacity: 0 },
      { opacity: 1, offset: 0.15 },
      { transform: `translate(${b.x}px, ${b.y}px) translate(-50%, -50%) scale(1)`, opacity: 1 },
    ], { duration: 560, easing: "cubic-bezier(.3,.7,.2,1)" });
    animation.onfinish = () => { envelope.remove(); resolve(); };
  });
}

function bump(slot) {
  slot.classList.remove("bump");
  void slot.offsetWidth; // restart the CSS animation
  slot.classList.add("bump");
}

async function animateRoute(ev) {
  const desk = byId(".desk", ev.publisher);
  const from = desk ? desk.querySelector(".desk-head") : $("#desks-h");
  await Promise.all(ev.targets.map(async (target, i) => {
    const slot = byId(".slot", target.id);
    if (!slot) return;
    await sleep(i * 90);
    await fly(from, slot.querySelector(".slot-box"));
    bump(slot);
    if (target.online) {
      const inbox = byId(".phone", target.id)?.querySelector(".inbox");
      await fly(slot.querySelector(".slot-box"), inbox);
    }
  }));
}

// --- connection to the dashboard server -------------------------------------------------------------------
function setBanner(text) {
  const banner = $("#banner");
  banner.textContent = text;
  banner.hidden = !text;
}

function connect() {
  const source = new EventSource("/events");
  source.onopen = () => { state.connected = true; };
  source.onmessage = (e) => handle(JSON.parse(e.data));
  source.onerror = () => {
    if (state.connected) setBanner("Lost the connection to the dashboard server. It reconnects automatically; if not, check the terminal where it runs.");
    state.connected = false;
  };
}

async function init() {
  const res = await fetch("/api/scenario");
  state.scenario = await res.json();
  buildDesks();
  buildWall();
  buildPhones();
  wireHeader();
  resetDynamic();
  render();
  tickClocks();
  setInterval(tickClocks, 15000);
  connect();
}

init().catch((err) => setBanner(`The dashboard could not load: ${err.message}`));
