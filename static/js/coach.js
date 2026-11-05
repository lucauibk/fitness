/** Coach-Chat: Fragen an den Coach und die Nachrichten des Morgen-Coaches in einem Verlauf.
 *  Die Antwort kommt als Server-Sent-Events per fetch (EventSource kann kein POST). */
import { $, api, esc, getProfile, icon, openSheet, toast } from "./util.js";

const STARTERS = ["Wie lief meine letzte Einheit?", "Was soll ich heute essen?",
  "Ich möchte eine Übung tauschen"];
const SESSION_STARTERS = ["Ist das Gewicht zu schwer?", "Gerät besetzt – Alternative?", "Wie viele Sätze noch?"];
const SOURCE = { daily: "Morgen-Check", chat: "", session: "Training" };

let sheet = null;
// Waehrend einer Einheit: Bruecke zum Entwurf (training.js) – snapshot, check/apply, undo
let session = null;
const known = new Map();    // id -> Nachricht, fuer die Vorschlaege beim Antippen
let list = null;
let oldest = null;
let more = false;
let sending = false;

const time = (iso) => new Date(iso).toLocaleString("de-AT", { weekday: "short", hour: "2-digit", minute: "2-digit" });
/** Kleines Markdown fuer Coach-Antworten: Absaetze, **fett** und "- "/"1. "-Listen.
 *  Erst escapen, dann formatieren – HTML vom Modell landet nie ungeprueft im DOM. */
export function prose(text) {
  const inline = (t) => esc(t).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
  const BULLET = /^\s*[-•*]\s+/;
  const NUMBER = /^\s*\d+[.)]\s+/;
  return String(text || "").trim().split(/\n{2,}/).map((block) => {
    // Absatz und Liste duerfen ohne Leerzeile aufeinander folgen: Zeilen gruppieren
    const groups = [];
    for (const line of block.split("\n")) {
      const kind = BULLET.test(line) ? "ul" : NUMBER.test(line) ? "ol" : "p";
      const last = groups.at(-1);
      if (last?.kind === kind) last.lines.push(line);
      else groups.push({ kind, lines: [line] });
    }
    return groups.map(({ kind, lines }) => (kind === "p"
      ? `<p>${lines.map(inline).join("<br>")}</p>`
      : `<${kind}>${lines.map((l) => `<li>${inline(l.replace(kind === "ul" ? BULLET : NUMBER, ""))}</li>`).join("")}</${kind}>`)).join("");
  }).join("");
}

// Waehrend des Streamens den Vorschlags-Block ausblenden – die Knoepfe kommen mit der fertigen Nachricht.
const visible = (text) => text.replace(/<aktionen>[\s\S]*$/, "").replace(/<a?k?t?i?o?n?e?n?$/, "");

const ACTION_STATE = { applied: "Ersetzt", undone: "Zurückgenommen", skipped: "Andere Option gewählt" };

const kg = (v) => String(v).replace(".", ",");
const isSession = (a) => a.type === "session_swap" || a.type === "session_sets";

/** Vorschlag in Worten: Tausch (dauerhaft oder nur heute) oder neue offene Saetze. */
function actionLine(a) {
  if (a.type === "session_sets") {
    const what = [a.sets ? `${a.sets} Sätze` : null, a.reps ? `${a.reps} Wdh.` : null,
      a.weight != null ? `${kg(a.weight)} kg` : null].filter(Boolean).join(" · ");
    return `<b>${esc(a.from)}</b>: ${esc(what)}`;
  }
  return `<s>${esc(a.from)}</s> → <b>${esc(a.to)}</b>`;
}

function actionButtons(m, a, i) {
  const key = `${m.id}:${i}`;
  if (isSession(a) && !session && a.state === "open") return `<span class="hint">Nur während der Einheit</span>`;
  if (a.state === "open") {
    const label = a.type === "session_sets" ? `${icon("check")} Übernehmen`
      : a.type === "session_swap" ? `${icon("swap")} Tauschen` : `${icon("swap")} Ersetzen`;
    return `<button type="button" class="btn small primary" data-apply="${key}">${label}</button>`;
  }
  if (a.state === "applied") {
    const undo = !isSession(a) || session?.canUndo(key);
    return `<span class="pill ok">${icon("check")} ${a.type === "session_sets" ? "Übernommen" : a.type === "session_swap" ? "Getauscht" : "Ersetzt"}</span>
      ${undo ? `<button type="button" class="btn small ghost" data-undo="${key}">Rückgängig</button>` : ""}`;
  }
  return `<span class="hint">${ACTION_STATE[a.state]}</span>`;
}

function actionCards(m) {
  if (!m.actions?.length) return "";
  return `<div class="chat-actions">${m.actions.map((a, i) => `<div class="chat-action ${a.state}">
    <small>${esc(a.day)} · ${a.type === "session_sets" ? "Sätze anpassen" : a.type === "session_swap" ? "tauschen" : "ersetzen"}</small>
    <div class="swap-line">${actionLine(a)}</div>
    <div class="chat-action-row">${actionButtons(m, a, i)}</div>
  </div>`).join("")}</div>`;
}

function bubble(m) {
  if (m.id) known.set(m.id, m);
  const mine = m.role === "user";
  return `<div class="bubble ${mine ? "mine" : "coach"} ${m.status === "error" ? "failed" : ""} ${m.actions?.length ? "has-actions" : ""}" data-id="${m.id}">
    <div class="bubble-text">${mine ? `<p>${esc(m.text).replace(/\n/g, "<br>")}</p>` : prose(m.text)}</div>
    ${actionCards(m)}
    <small>${SOURCE[m.source] ? `${SOURCE[m.source]} · ` : ""}${time(m.created_at)}</small></div>`;
}

async function runAction(button, what) {
  const key = button.dataset[what];
  const [id, index] = key.split(":");
  const a = known.get(Number(id))?.actions?.[Number(index)];
  const local = a && isSession(a);
  button.disabled = true;
  try {
    // Nur heute: erst pruefen, ob der Entwurf noch passt, dann festhalten, dann im Entwurf aendern
    if (local) {
      if (!session) throw new Error("Die Einheit läuft nicht mehr.");
      if (what === "apply") session.check(a);
      else session.checkUndo(key);
    }
    const res = await api(`coach/messages/${id}/actions/${index}/${what}`, { method: "POST" });
    if (local) {
      if (what === "apply") session.apply(a, key);
      else session.undo(key);
    }
    list.querySelector(`.bubble[data-id="${id}"]`).outerHTML = bubble(res.message);
    toast(what === "undo" ? "Zurückgenommen" : local ? "In der Einheit geändert" : "Im Plan ersetzt");
    if (!local) window.dispatchEvent(new CustomEvent("data-changed"));
  } catch (e) {
    button.disabled = false;
    toast(e.offline ? "Dafür ist eine Verbindung nötig." : e.message, { error: true });
  }
}

function emptyState() {
  const intro = session
    ? "Kurze Frage zwischen zwei Sätzen? Der Coach sieht deine laufende Einheit und kann sie für heute anpassen."
    : `Frag deinen Coach alles zu Training, Ernährung und Erholung – er kennt deinen Plan,
      deine Einheiten und deine Check-ins. Jeden Morgen meldet er sich hier, wenn es etwas Neues gibt.`;
  return `<div class="chat-empty" data-empty>
    <p class="hint">${intro}</p>
    <div class="chips">${(session ? SESSION_STARTERS : STARTERS).map((s) => `<button type="button" class="chip" data-starter>${esc(s)}</button>`).join("")}</div>
  </div>`;
}

function scrollDown() { list.scrollTop = list.scrollHeight; }

async function markSeen(messages) {
  const last = messages.filter((m) => m.role === "coach").at(-1);
  if (!last) return;
  try {
    await api("coach/seen", { method: "POST", body: { id: last.id } });
    window.dispatchEvent(new CustomEvent("coach-seen"));
  } catch { /* nur der "neu"-Punkt – egal, wenn es nicht klappt */ }
}

async function loadOlder() {
  if (!more || !oldest) return;
  more = false;
  const before = list.scrollHeight;
  const res = await api(`coach/messages?before=${oldest}`);
  if (!res.messages.length) return;
  oldest = res.messages[0].id;
  more = res.messages.length === res.page;
  list.insertAdjacentHTML("afterbegin", res.messages.map(bubble).join(""));
  list.scrollTop += list.scrollHeight - before;     // Leseposition halten
}

/** Liest den SSE-Strom: "event: x\ndata: {...}\n\n". */
async function* events(res) {
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buf += dec.decode(value, { stream: true });
    let cut;
    while ((cut = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, cut);
      buf = buf.slice(cut + 2);
      const ev = /^event: (.*)$/m.exec(block)?.[1];
      const data = /^data: (.*)$/m.exec(block)?.[1];
      if (ev && data) yield [ev, JSON.parse(data)];
    }
  }
}

async function send(text) {
  text = text.trim();
  if (!text || sending) return;
  sending = true;
  const form = $("#coachForm", sheet);
  form.querySelector("button").disabled = true;
  $("[data-empty]", list)?.remove();
  list.insertAdjacentHTML("beforeend", bubble({ id: "", role: "user", text, source: session ? "session" : "chat", created_at: new Date().toISOString() }));
  list.insertAdjacentHTML("beforeend", `<div class="bubble coach pending" data-pending>
    <div class="bubble-text"><span class="typing" aria-label="Coach schreibt"><i></i><i></i><i></i></span></div></div>`);
  const pending = $("[data-pending]", list);
  const out = $(".bubble-text", pending);
  scrollDown();
  let got = "";
  try {
    const res = await fetch(`/api/coach/chat?profile=${getProfile()}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, session: session ? session.snapshot() : undefined }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(Array.isArray(data.detail) ? "Ungültige Eingabe." : data.detail || `Fehler ${res.status}`);
    }
    for await (const [ev, data] of events(res)) {
      if (ev === "delta") {
        got += data.text;
        out.innerHTML = prose(visible(got));
        scrollDown();
      } else {
        pending.outerHTML = bubble(data.message);
        if (ev === "done") markSeen([data.message]);
      }
    }
    form.reset();
  } catch (e) {
    pending.remove();
    list.lastElementChild?.remove();          // die eigene Nachricht – sie ist nicht angekommen
    $("[name=text]", form).value = text;
    toast(e instanceof TypeError ? "Server nicht erreichbar." : e.message, { error: true });
  } finally {
    sending = false;
    form.querySelector("button").disabled = false;
    scrollDown();
    window.dispatchEvent(new CustomEvent("coach-seen"));
  }
}

/** opts.session: waehrend einer Einheit (training.js) – kurze Antworten, Vorschlaege fuer heute,
 *  gezeigt werden die Nachrichten seit Beginn der Einheit (aeltere per Hochscrollen). */
export async function openCoach(opts = {}) {
  session = opts.session || null;
  known.clear();
  sheet = openSheet({
    title: `${icon("chat")} Coach${session ? " · Training" : ""}`,
    body: `<div class="chat-list" role="log" aria-live="polite"><p class="hint pad">Lädt …</p></div>`,
    footer: `<form id="coachForm" class="chat-form">
      <textarea name="text" rows="1" maxlength="1000" placeholder="${session ? "Kurze Frage zur Einheit …" : "Frag deinen Coach …"}" aria-label="Nachricht an den Coach"></textarea>
      <button type="submit" class="icon-btn send" aria-label="Senden">${icon("up")}</button></form>`,
  });
  sheet.classList.add("chat-sheet");
  sheet.classList.toggle("compact", Boolean(session));
  list = $(".chat-list", sheet);
  const form = $("#coachForm", sheet);
  const input = $("[name=text]", form);
  const grow = () => { input.style.height = "auto"; input.style.height = `${Math.min(input.scrollHeight, 140)}px`; };
  input.addEventListener("input", grow);
  input.addEventListener("keydown", (ev) => {
    // Enter sendet am Rechner, Umschalt+Enter macht eine neue Zeile; am Handy bleibt Enter Zeilenumbruch
    if (ev.key === "Enter" && !ev.shiftKey && matchMedia("(hover: hover)").matches) {
      ev.preventDefault();
      form.requestSubmit();
    }
  });
  form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    send(input.value).then(grow);
  });
  sheet.addEventListener("click", (ev) => {
    const s = ev.target.closest("[data-starter]");
    if (s) return send(s.textContent);
    const apply = ev.target.closest("[data-apply]");
    if (apply) return runAction(apply, "apply");
    const undo = ev.target.closest("[data-undo]");
    if (undo) return runAction(undo, "undo");
  });
  list.addEventListener("scroll", () => { if (list.scrollTop < 60) loadOlder().catch(() => {}); });
  try {
    const res = await api("coach/messages");
    const since = session?.since();
    const shown = since ? res.messages.filter((m) => m.created_at >= since.slice(0, 19)) : res.messages;
    oldest = shown[0]?.id ?? res.messages.at(-1)?.id ?? null;
    more = shown.length < res.messages.length || res.messages.length === res.page;
    if (!shown.length && oldest) oldest += 1;      // ab der neuesten alles Aeltere nachladbar
    list.innerHTML = shown.length ? shown.map(bubble).join("") : emptyState();
    scrollDown();
    markSeen(res.messages);
  } catch (e) {
    list.innerHTML = `<p class="hint pad">${esc(e.offline ? "Offline – der Coach braucht eine Verbindung." : e.message)}</p>`;
  }
}
