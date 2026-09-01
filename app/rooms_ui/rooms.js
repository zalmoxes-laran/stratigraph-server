/**
 * THE NODE'S FRONT DOOR — sign in, see what is here, and go somewhere.
 *
 * This file is the room browser's, grown. What it gained is a HEAD (which node
 * is this, who am I on it, what does it run) and two more scopes beside the
 * rooms — the studies and the monuments.
 *
 * **It owns nothing.** Not a list of services, not an address of a neighbour,
 * not a rule about who may see what. Every one of those is asked:
 *
 *   · `/v1/node` says what this node offers, WITH LIVE STATES and with public
 *     addresses where the deployment published one. A service that is down
 *     appears down; one whose address nobody configured appears without a link,
 *     because a guessed path is a button that 404s;
 *   · `/v1/rooms` is ALREADY ACL-filtered. There is no visibility logic here and
 *     there must not be: a second implementation of the rule, in a place that is
 *     not the one that decides, is a rule with two answers;
 *   · every door — a room's, a study's — is ASKED (`…/open`) and never
 *     assembled. There is a test in another repository that forbids exactly the
 *     hand-built `stratigraph://`.
 *
 * **It imports the node console's sign-in module rather than copying it**
 * (`../admin/auth.js`, relative so the `/em` prefix a proxy adds comes along).
 * One PKCE implementation for the faces this server serves.
 *
 * **What it never holds:** a token on disk. The access token lives in a
 * module-scoped variable for this tab, refreshed at 80% of its lifetime, and the
 * links it produces carry no credential at all — that is the handoff contract,
 * not a convention (`app/handoff.py`).
 */

import * as oidc from "../admin/auth.js";
// Six languages, English the source — the SAME dictionary the operator console
// uses, imported the same way `auth.js` is (`../admin/…`, relative so the `/em`
// prefix a proxy adds comes along). Two faces of one server, one dictionary:
// two would drift, and the one that drifted would be the one nobody tested.
import { LOCALE, mountPicker, t } from "../admin/i18n.js";

/** The API, derived from where this page is served — the same reasoning (and the
 *  same measured trap) as the console's: at `/em/rooms/index.html` a naive strip
 *  produces `/em/rooms/index.html/v1` and every call 404s behind a "Loading…".
 *
 *  Taken from the document's own DIRECTORY rather than from its path, which is
 *  what makes `index.html` and the bare directory the same case (the chatbot's
 *  page learned this first). Measured on the three spellings that exist:
 *
 *      /em/rooms/  → /em/v1      behind the node's Caddy
 *      /rooms/     → /v1         bare, `uvicorn --port 8000`
 *      /em/rooms/index.html      the directory is still /em/rooms/
 *
 *  …AND FROM EVERY DOOR, not only from `/rooms/`. Since the pages separated by
 *  verb (`/work/`, `/tools/`) the same script runs at three addresses, and a
 *  regex that only knew one of them derived `/em/work/v1` — every call 404ing
 *  behind a "Loading…", which is precisely the trap this comment already
 *  described one spelling ago.
 */
const DOORS = /(?:rooms|work|tools)\/$/;
const BASE = new URL(".", window.location.href).pathname
  .replace(DOORS, "").replace(/\/$/, "") + "/v1";

let token = "";
let refreshToken = "";
let refreshTimer = 0;
let authConfig = null;
let node = null;          // what /v1/node answered: this node's own description
let me = null;
//: the last room listing, kept ONLY so a change of language repaints without
//: a round trip to the node. Never a source of truth: every render replaces it.
let LAST_ROOMS = null;
//: whether the NODE said this caller is an operator (`/v1/admin/whoami`). The
//: map is gated on it and so is the «amministrare» door, from the same answer —
//: two gates asking twice is two gates that can disagree.
let operator = false;

const $ = (id) => document.getElementById(id);
const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

// ── the node, with the token attached once ──────────────────────────────────

async function request(method, path, body) {
  return await call(`${BASE}${path}`, method, body);
}

/** …and the same call to an ABSOLUTE address, for the catalogue next door. The
 *  address is never written here: it comes from `/v1/node`. */
async function call(url, method = "GET", body) {
  const answer = await fetch(url, {
    method,
    headers: {
      Accept: "application/json",
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  const text = await answer.text();
  let payload = null;
  try { payload = text ? JSON.parse(text) : null; } catch { payload = null; }
  if (!answer.ok) {
    const error = new Error((payload && payload.detail) || `HTTP ${answer.status}`);
    error.status = answer.status;
    throw error;
  }
  return payload;
}

/** Where the catalogue is, according to the NODE. "" when this deployment
 *  published none — and then the two catalogue zones stay away rather than
 *  guessing a path beside our own. */
function catalogBase() {
  const offer = (node?.offers || []).find((o) => o.name === "stratigraph-catalog");
  return (offer && offer.state !== "not configured" && offer.url) || "";
}

// ── session ─────────────────────────────────────────────────────────────────

function scheduleRefresh(seconds) {
  window.clearTimeout(refreshTimer);
  if (!refreshToken || !seconds) return;
  const wait = Math.max(15, Math.floor(seconds * 0.8)) * 1000;
  refreshTimer = window.setTimeout(async () => {
    const result = await oidc.refresh(authConfig, refreshToken);
    if (!result.ok) {
      note($("gate-note"), t("session.notRefreshed", { error: result.error }), true);
      return;
    }
    token = result.token;
    refreshToken = result.refresh_token || refreshToken;
    scheduleRefresh(result.expires_in);
  }, wait);
}

function adoptSession(result) {
  token = result.token;
  refreshToken = result.refresh_token || "";
  scheduleRefresh(result.expires_in);
}

function note(element, text, bad = false) {
  if (!element) return;
  element.textContent = text || "";
  element.classList.toggle("warn", Boolean(bad));
}

// ── THE CARD — one component, three scopes ──────────────────────────────────
//
// A monument, a study and a room have the same shape: a name, a subject, who may
// see it, and a set of doors. They differ by the VERB, which is what tells them
// apart on screen — esplora · leggi · entra — and by where the doors come from.
//
// If a second card ever appears in this file, the road that multiplies has been
// taken: the three are the same thing at three moments of its life, and the
// component is the place that says so.

function card({ title, id, tag, notes = [], verb, build }) {
  const box = el("div", "room");
  const head = el("div", "room-head");
  head.append(el("span", "room-title", title));
  if (id) head.append(el("span", "room-id", id));
  if (verb) head.append(el("span", "verb", verb));
  if (tag) head.append(el("span", "role", tag));
  box.append(head);
  for (const line of notes.filter(Boolean)) box.append(el("p", "note", line));
  const actions = el("div", "room-actions");
  const said = el("span", "note");
  box.append(actions);
  if (build) build(actions, said, box);
  actions.append(said);
  return box;
}

/** A tool NAME with up to two doors, the shape both scopes use. */
function toolGroup(label, doors) {
  const group = el("span", "tool");
  group.append(el("span", "tool-name", label));
  for (const [name, title, go] of doors) {
    const button = el("button", "", name);
    button.title = title;
    button.addEventListener("click", go);
    group.append(button);
  }
  return group;
}

// ── zone 1 · the rooms — where the work is happening now ────────────────────

function renderRooms(rooms) {
  LAST_ROOMS = rooms;
  // ONE SCRIPT, THREE PAGES: each zone draws only where its host exists, and is
  // silent where it does not. The alternative — a script per page — is three
  // copies of the session, the token refresh and the language, i.e. three places
  // for the same bug. The listing is still KEPT (`LAST_ROOMS`) so a language
  // change repaints without a round trip even if this page has no list.
  const host = $("rooms");
  if (!host) return;
  host.innerHTML = "";
  const live = (rooms || []).filter((r) => !r.archived_at);
  $("zone-rooms").hidden = false;
  if (!live.length) {
    host.append(el("p", "note",
      t("rooms.none")));
    return;
  }
  for (const room of live) host.append(roomCard(room));
}

function roomCard(room) {
  return card({
    title: room.title || room.room_id,
    id: room.room_id,
    tag: room.your_role || "—",
    verb: t("rooms.verb"),
    // REPORTED, never hidden: a workspace whose container was moved still
    // exists, and the honest answer is the name of what is missing.
    notes: (room.missing_refs || []).length
      ? [t("rooms.missingRefs", { refs: room.missing_refs.join(", ") })] : [],
    build(actions, said, box) {
      // The three consumers, from the server's own list (`handoff.CONSUMERS`).
      // Written out rather than looped from the answer because the answer
      // arrives only when a button is pressed, and a row with no buttons until
      // you press one is a row with no buttons.
      for (const [tool, label] of [["emstudio", "EMStudio"],
                                   ["blender", "EMtools"],
                                   ["chatbot", "Field assistant"]]) {
        const group = toolGroup(label, [[t("door.desktop"),
          t("door.desktop.title", { tool: label }),
          () => void openIn(room, tool, box, said)]]);
        group.dataset.tool = tool;
        actions.append(group);
      }
      const copy = el("button", "ghost", t("door.copy"));
      copy.addEventListener("click", () => void copyLink(room, said));
      actions.append(copy);
      // Asked once, up front, so the browser doors are there before anybody
      // presses anything. A failure is silent HERE on purpose: it costs only the
      // extra door, and a red line on every card because one node has no
      // EM_PUBLIC_BASE would be noise about a room that is fine.
      void addBrowserDoors(room, box);
    },
  });
}

/** Draw "browser" beside "desktop" for every tool that has a web build.
 *
 *  What decides is the SERVER's answer (`tools[t].browser`), never this page:
 *  whether a web app is deployed is a fact about the deployment, and a client
 *  that assumed one would offer a button that 404s. */
async function addBrowserDoors(room, box) {
  let targets;
  try { targets = await handoff(room); } catch { return; }
  for (const [tool, target] of Object.entries(targets.tools || {})) {
    if (!target.browser) continue;          // no web build: no button, no lie
    const group = box.querySelector(`.tool[data-tool="${tool}"]`);
    if (!group || group.querySelector(".browser")) continue;
    const button = el("button", "browser", t("door.browser"));
    button.title = t("door.browser.title", { tool: target.label, url: target.browser });
    button.addEventListener("click", () => {
      // A plain navigation with the same two parameters the scheme carries and
      // the same absence of a token: the web build signs itself in.
      window.open(target.browser, "_blank", "noopener");
    });
    group.append(button);
  }
}

async function handoff(room) {
  // The SERVER builds the link, not this page. It knows its own public address
  // (configuration, never a request header — a link built from `Host` is a link
  // an attacker can aim), and one builder means the consumers are measured
  // against one grammar.
  return await request("GET", `/rooms/${encodeURIComponent(room.room_id)}/open`);
}

/**
 * FOLLOW A CUSTOM SCHEME, and say so when nothing happens.
 *
 * A `stratigraph://` navigation on a machine with no handler does NOTHING — no
 * error, no event, no page — and looks exactly like the user's fault. So: watch
 * for the window losing focus (which is what an OS handing over to an app looks
 * like from in here) and, if it never does, say what did not happen and show the
 * link so it can be pasted where a handler is.
 *
 * ONE implementation, and that is the point of this function existing. It was
 * inline in `openIn`, so a ROOM said «Nothing opened» after 1.8 s and a STUDY
 * said nothing at all — two doors on the same page with two different degrees of
 * honesty, which is worse than either.
 */
function followScheme(link, box, said) {
  showLink(box, link);
  let left = false;
  const onBlur = () => { left = true; };
  window.addEventListener("blur", onBlur, { once: true });
  window.location.href = link;
  window.setTimeout(() => {
    window.removeEventListener("blur", onBlur);
    if (left) return;
    note(said, t("door.nothingOpened", { scheme: link.split(":")[0] }), true);
  }, 1800);
}

async function openIn(room, tool, box, said) {
  let targets;
  try { targets = await handoff(room); }
  catch (error) { note(said, error.message, true); return; }
  followScheme(targets.scheme, box, said);
}

async function copyLink(room, said) {
  let targets;
  try { targets = await handoff(room); }
  catch (error) { note(said, error.message, true); return; }
  try {
    await navigator.clipboard.writeText(targets.web);
    note(said, t("door.copied"));
  } catch { note(said, targets.web); }
}

function showLink(box, text) {
  let row = box.querySelector(".link-row");
  if (!row) { row = el("div", "link-row"); box.append(row); }
  row.innerHTML = "";
  row.append(el("code", "", text));
}

// ── CONSULTARE lives in the CATALOGUE, and no longer here ────────────────────
//
// The studies zone and the monuments zone were CUT on 6 September 2026, and it is
// a cut and not a loss: `/catalog/ui/` already lists the published studies and
// already has the by-HDT view — measured before removing anything, because
// «somewhere else has it» is the sentence people say about the thing that then
// turns out to exist nowhere.
//
// The reason is the design note's, not tidiness: the door did four jobs at once,
// so whoever arrived for one walked through three. The pages separate by VERB —
// consultare (the catalogue), lavorare (`/work/`), attrezzarsi (`/tools/`),
// amministrare (`/admin/` and the map) — and the door became the vestibule that
// says who you are, how the node is, and where to go.
//
// What went with them, measured after cutting rather than assumed: `loadStudies`,
// `studyCard`, `studyDoors`, `loadMonuments`, `monumentCard` — some 150 lines
// that had a second implementation one service away — AND TWO SYMBOLS THAT WERE
// LEFT WITHOUT A CALLER:
//
//   `SHOWN`, the truncation cap. Only the two cut zones truncated; the rooms
//     list never did (it filters the archived and draws the rest). Keeping the
//     constant «because the rooms list truncates too» was a sentence I wrote and
//     the grep disproved.
//   `entityText`, the guard against `[object Object]` in a title. Its callers
//     were the study and monument cards; a room's title is `room.title ||
//     room.room_id`, two strings.
//
// DECLARED, because it is a defence this repo no longer has: the equivalent
// guards for a published study's card — the object-in-a-title chain, the
// truncation sentence with both numbers — do NOT exist in the catalogue's UI
// suite (its tests cover the HDT view at the index and API level, and its page
// for brand and locales). That is a debt in `stratigraph-catalog`, named here
// rather than quietly inherited.

// ── THE VESTIBULE · where to go, for whoever just arrived ────────────────────
//
// The property this door had conquered, and the reason the note refuses «four
// equal pages»: you arrive and you understand where everything is. So the door
// keeps an overview of the four verbs and owns none of them.
//
// IT STILL OWNS NOTHING. `consultare` is the CATALOGUE's address as the node
// declares it (`/v1/node`, `offers`), and it is absent — not guessed — on a
// deployment that published none. `amministrare` likewise. The two it names
// itself, `lavorare` and `attrezzarsi`, are its own sibling pages: a page
// knowing its own site is not the same thing as a page knowing where Keycloak
// lives, and that is the whole distinction this file has defended.
//
// AND IT SENDS, rather than merely listing: signed in, the work is first;
// not signed in, the published is — because that is what an unsigned visitor
// can actually have, and an emphasis on a door that will refuse them is an
// invitation to a locked room.

function destinationCard({ label, sub, href, primary }) {
  const box = el("a", primary ? "dest dest-primary" : "dest");
  box.href = href;
  box.append(el("span", "dest-label", label));
  box.append(el("span", "dest-sub", sub));
  return box;
}

function renderDestinations() {
  const host = $("destinations");
  if (!host) return;                      // not the vestibule: nothing to draw
  const catalog = catalogBase();
  const signed = Boolean(token);

  const doors = [
    // LAVORARE — first when there is somebody to work as
    { key: "work", href: "../work/", primary: signed,
      label: t("go.work"), sub: t("go.work.sub") },
    // CONSULTARE — first when there is not: the published is what a visitor has
    catalog
      ? { key: "consult", href: `${catalog}/ui/`, primary: !signed,
          label: t("go.consult"), sub: t("go.consult.sub") }
      // NOT A DEAD CARD. A node with no catalogue has nothing published to
      // consult, and drawing the door anyway would send somebody to a 404 in the
      // name of symmetry.
      : null,
    { key: "tools", href: "../tools/", primary: false,
      label: t("go.tools"), sub: t("go.tools.sub") },
    // AMMINISTRARE — only for whoever the node has told us is an operator. The
    // map below is gated the same way and by the same answer, so the two cannot
    // disagree about who is one.
    operator
      ? { key: "admin", href: "../admin/", primary: false,
          label: t("go.admin"), sub: t("go.admin.sub") }
      : null,
  ].filter(Boolean);

  // the emphasised one leads
  doors.sort((a, b) => Number(b.primary) - Number(a.primary));
  host.replaceChildren(...doors.map(destinationCard));
}

// ── zone 4 · THE NODE MAP — for whoever came to make this work ──────────────
//
// The three zones above are for somebody who came to WORK. This one is for
// somebody who came to make the node work, and it exists because that person's
// twenty questions a day — is Keycloak up, is the realm the right one, does MinIO
// have the bucket, where is the OpenAPI, is the catalogue answering or is Caddy
// answering for it — live in three places that are not this page: a cheatsheet,
// `docker ps`, and the memory of whoever brought the stack up.
//
// THREE RULES, and they are the whole design:
//
//  1. **the node declares, the page composes.** Not one address is written here.
//     Every row comes from `/v1/admin/health`, which is the node describing
//     itself, so a deployment that moves Keycloak moves this map with it and
//     nobody edits this file.
//  2. **no URL is guessed.** A face whose browser address nobody configured is
//     drawn WITHOUT a link, with its state and its internal address. An
//     `http://localhost:9001` written for convenience is a button that works on
//     one laptop — a default that is an assertion.
//  3. **read only.** No verb here writes. This zone goes to the institutional
//     node as it is.
//
// It is asked for ONLY when the node has said `operator: true`. A non-operator
// does not see an error: they do not see the zone, and the request is never made.

async function loadNodeMap() {
  if (!$("zone-map")) {
    // NOT THE VESTIBULE. The question is still worth asking where the
    // «amministrare» door is gated on the same answer — but only there: on a page
    // with neither a map nor a destination to reveal, `/admin/whoami` is a
    // request made for its side effect, and it answers 401 without a session.
    // Measured in the browser: three red lines in a console on a page that needs
    // no session at all, which teaches whoever opens that console to stop
    // reading it.
    if (!$("destinations")) return;
    try {
      const who = await request("GET", "/admin/whoami");
      if (who && who.operator === true) { operator = true; renderDestinations(); }
    } catch { /* not an operator, or no session: neither is a fault */ }
    return;
  }
  // `/admin/whoami` answers WITHOUT a 403 — that is why the console asks it
  // before drawing anything — so this is a question, not an attempt.
  let who = null;
  try { who = await request("GET", "/admin/whoami"); } catch { return; }
  if (!who || who.operator !== true) return;
  operator = true;
  renderDestinations();       // …and now the door for it exists, from this answer

  let report;
  try { report = await request("GET", "/admin/health"); }
  catch (error) {
    // an operator whose own map will not load has to be told, because they are
    // the one person who cannot tell a broken page from a broken node
    $("zone-map").hidden = false;
    note($("map-note"), t("map.unreachable", { error: error.message }), true);
    return;
  }

  $("zone-map").hidden = false;
  note($("map-note"), t("map.verdict", {
    verdict: report.verdict, deadline: report.deadline_s,
  }), report.verdict !== "ok");

  const entrances = $("map-entrances");
  entrances.innerHTML = "";
  for (const face of report.entrances || []) {
    entrances.append(mapRow({
      label: face.label, detail: face.what,
      browser: face.url,
      // the PUBLIC address when the node has one — a curl on that works from
      // anywhere, which is the difference from a neighbour's probe URL. The path
      // alone when it does not, which is still the useful half.
      internal: face.url || face.path,
      // an entrance is always there to be asked — this server is answering right
      // now, which is what serving this page means
      curlable: Boolean(face.url),
      // …and a public entrance is reachable from wherever the operator is
      fromNode: false,
    }));
  }

  const neighbours = $("map-neighbours");
  neighbours.innerHTML = "";
  for (const check of report.checks || []) {
    neighbours.append(mapRow({
      label: check.name, state: check.state, detail: check.detail,
      browser: check.browser,
      // the address to SHOW: what the probe asked, or — for a service that was
      // not asked at all — the address it WOULD use, which is what an operator
      // wants to see next to «off by choice».
      internal: check.probe || check.target,
      // …but the CURL is offered only for a question that was actually put.
      // Measured on the map: the engine row read «off by choice» and still
      // offered `copy curl`, i.e. offered to demonstrate a failure that is not a
      // fault — on a service we had just said nobody is running.
      curlable: Boolean(check.probe),
      // a probe dials the node's own network — always
      fromNode: true,
      facts: check.facts,
    }));
  }
}

/**
 * One row of the map: what it is, how it is, where a browser goes, and the exact
 * question a terminal can put.
 *
 * The `curl` is FORMATTED here from the URL the node already gave us — that is
 * formatting, not knowledge, and it adds nothing to keep aligned. Its value is
 * the comparison: if the row and the terminal disagree, the row is lying, and
 * that is the bug worth finding.
 */
function mapRow({ label, state, detail, browser, internal, facts,
                  fromNode, curlable }) {
    const row = el("div", "map-row");
  const head = el("div", "map-row-head");
  head.append(el("span", "map-label", label));
  // THE NODE'S OWN WORDS, untranslated. A state this page rephrased would be a
  // second vocabulary for one fact, and the operator comparing the page with
  // `docker ps` needs the same word in both.
  if (state) head.append(el("span", `map-state st-${state.replace(/\s+/g, "-")}`, state));
  row.append(head);
  if (detail) row.append(el("p", "note", detail));

  const links = el("div", "map-links");
  if (browser) {
    const a = el("a", "map-open", t("map.open"));
    a.href = browser;
    a.target = "_blank";
    a.rel = "noopener";
    a.title = browser;
    links.append(a);
  } else {
    // NO LINK, and said: this is rule 2 made visible rather than silent.
    links.append(el("span", "note map-nolink", t("map.noBrowser")));
  }
  if (internal) {
    links.append(el("code", "map-internal", internal));
    if (curlable && /^https?:\/\//.test(internal)) {
      const copy = el("button", "map-curl", t("map.curl"));
      copy.title = t("map.curlTitle");
      copy.addEventListener("click", async () => {
        // WHERE it has to be run is part of the answer. A probe's URL is on the
        // node's OWN network — measured: `curl http://minio:9000/...` from the
        // operator's laptop gets nothing, and the same line run where the node
        // runs gets the 200 this row is reporting. A button that copied the line
        // without saying so would send somebody off to debug DNS.
        //
        // Said as a shell COMMENT, so the two lines paste as one thing — and
        // without naming docker: how the node is run is not this page's business.
        //
        // And `fromNode` comes from the CALLER, not from sniffing the hostname.
        // The first version matched `localhost|127.0.0.1|…` here and
        // `tests/test_node_front_door.py` refused it — rightly: a page that
        // guesses which addresses are routable is a page with an opinion about
        // the deployment. The data already knows: a PROBE url is on the node's
        // network by definition, and an ENTRANCE url is public by definition.
        const line = (fromNode ? `# ${t("map.curlWhere")}\n` : "")
          + `curl -s -o /dev/null -w '%{http_code} %{content_type}\\n' ${internal}`;
        try {
          await navigator.clipboard.writeText(line);
          copy.textContent = t("map.copied");
          window.setTimeout(() => { copy.textContent = t("map.curl"); }, 1600);
        } catch {
          // no clipboard permission: show it, so it can be selected by hand
          links.append(el("code", "map-internal", line));
        }
      });
      links.append(copy);
    }
  }
  row.append(links);

  // the facts a probe learned, when it learned any — a bucket's size, a realm's
  // key count. Never a secret: the probes do not collect one.
  const interesting = Object.entries(facts || {})
    .filter(([, v]) => v !== null && v !== undefined && typeof v !== "object");
  if (interesting.length) {
    row.append(el("p", "note map-facts",
      interesting.map(([k, v]) => `${k}: ${v}`).join(" · ")));
  }
  return row;
}

// ── the screen, repainted in the active language ────────────────────────────
//
// One function, called at boot and on every change of language. It repaints the
// STATIC labels; everything the page says at a moment is built with `t()` where
// it is built, so it is already in the right language — except the three lists,
// which are re-rendered from the data they already hold.
function paintStrings() {
  const set = (id, text) => { const n = $(id); if (n) n.textContent = text; };
  document.documentElement.lang = LOCALE;
  document.title = t("app.title") + " · StratiGraph";
  set("app-sub", t("app.sub"));
  set("gate-title", t("app.title"));
  set("gate-why", t("gate.why"));
  set("btn-signin", t("action.signin"));
  set("btn-signout", t("action.signout"));
  set("here-title", t("here.title"));
  set("here-sub", t("here.sub"));
  set("rooms-title", t("rooms.title"));
  set("rooms-sub", t("rooms.sub"));
  set("go-title", t("go.title"));
  set("go-sub", t("go.sub"));
  set("back-door", t("go.back"));
  set("map-title", t("map.title"));
  set("map-sub", t("map.sub"));
  set("map-entrances-head", t("map.entrances"));
  set("map-neighbours-head", t("map.neighbours"));
  set("btn-create", t("rooms.create"));
  const input = $("new-room-title");
  if (input) input.placeholder = t("rooms.newName");
  renderNodeLine();
  renderServices();
  renderDestinations();
  // …and the list, from what it is already holding: a language change must not
  // cost a round trip to the node.
  if (LAST_ROOMS) renderRooms(LAST_ROOMS);
}

// ── the head: which node is this, and what does it run ──────────────────────

function renderNodeLine() {
  if (!node) {
    note($("node-line"), t("node.silent"), true);
    return;
  }
  const where = node.public_base || window.location.origin;
  const auth = t(node.auth === "keycloak" ? "node.auth.on" : "node.auth.off");
  note($("node-line"), t("node.line", { service: node.service,
                                        version: node.version, where, auth }),
       node.auth !== "keycloak");
}

/** One capability a face declares about itself: which engine, and what it needs.
 *
 *  NOTHING HERE NAMES A CAPABILITY, and that is the whole property: this page
 *  keeps no list of which capabilities exist in the world, so the day the field
 *  assistant declares a third one it appears without anybody editing this file.
 *  Same discipline as the services above — the node declares, the page composes.
 *
 *  `name` and `state` are the neighbour's own words and are shown RAW: the line
 *  drawn on 2026-09-01 is that chrome is translated and what the NODE says is
 *  not, and a translated capability name would need that list. `engine` is a
 *  phrase the neighbour wrote; `needs` is the one word this page contributes.
 *
 *  A capability that is absent says WHAT WOULD CONFIGURE IT, because the whole
 *  rule this serves is that a function you do not have is named rather than
 *  hidden: «if the node has an AI you have functions; if it does not, you do not
 *  — and the page says so».
 */
function capabilityRow(capability) {
  const row = el("div", "capability");
  const head = el("div", "capability-head");
  head.append(el("span", "capability-name", capability.name || "?"));
  if (capability.state) {
    head.append(el("span", "capability-state", capability.state));
  }
  row.append(head);
  if (capability.engine) row.append(el("p", "note", capability.engine));
  const needs = (capability.missing || []).filter(Boolean);
  if (needs.length) {
    row.append(el("p", "note needs",
                  t("here.capability.needs") + ": " + needs.join(" · ")));
  }
  return row;
}

/** What the node RUNS — states and all — and what you install yourself. The two
 *  halves are kept apart because they answer different questions: the node knows
 *  whether its own services are up, and cannot know anything about the tool on
 *  your laptop. Two honest links beat a list that pretends to know. */
function renderServices() {
  const host = $("services");
  if (!host) return;              // «attrezzarsi» is its own page now
  host.innerHTML = "";
  for (const offer of node?.offers || []) {
    const item = el("div", `service state-${offer.state.replace(/ /g, "-")}`);
    const head = el("div", "service-head");
    // The LABEL is chrome, so it is translated — with the node's own as the
    // fallback, because a node may one day offer a face this page has no word
    // for, and printing its name beats printing a key.
    const label = t("service." + offer.name) || offer.label;
    head.append(el("span", "dot"), el("span", "service-name", label));
    head.append(el("span", "service-state", offer.state));
    item.append(head);
    // …the DETAIL is not translated: it is the node's own sentence, diagnostic
    // and in the source language, and it is shown only when there is something
    // to diagnose. On a healthy service the state word is the whole answer.
    if (offer.state !== "ok") item.append(el("p", "note", offer.detail));
    // …and WHAT THAT FACE CAN DO, when it declares it.
    for (const capability of offer.capabilities || []) {
      item.append(capabilityRow(capability));
    }
    if (offer.url) {
      const link = el("a", "more", t("here.go"));
      link.href = offer.url;
      item.append(link);
    }
    host.append(item);
  }

  const tools = $("tools-install");
  if (!tools) return;
  tools.innerHTML = "";
  for (const tool of node?.tools || []) {
    const item = el("div", "service state-install");
    const head = el("div", "service-head");
    // NO status dot, and that is the point of the whole half: a dot means "I
    // checked", and this node cannot check whether EMStudio is on your laptop.
    // A grey dot beside four live ones would read as a fifth service that is
    // down.
    head.append(el("span", "service-name", tool.label));
    head.append(el("span", "service-state", t("here.yours")));
    item.append(head);
    item.append(el("p", "note", t("here.cannotKnow")));
    const row = el("p", "");
    if (tool.download) {
      const a = el("a", "more", t("here.download")); a.href = tool.download; row.append(a);
    }
    if (tool.manual) {
      const a = el("a", "more", t("here.manual")); a.href = tool.manual; row.append(a);
    }
    item.append(row);
    tools.append(item);
  }
}


// ── ONE SILENT ATTEMPT PER DOOR ──────────────────────────────────────────────
//
// The marker outlives the redirect because `sessionStorage` does, and it dies
// with the tab, which is the right lifetime: a person who opens a new tab is a
// person who may have signed in meanwhile.
const SILENT_KEY = () => `sg.silenttry:${window.location.pathname}`;
function silentTried() {
  try { return sessionStorage.getItem(SILENT_KEY()) === "1"; } catch { return true; }
}
function markSilentTry() {
  try { sessionStorage.setItem(SILENT_KEY(), "1"); } catch { /* private window */ }
}
/** Every door's marker, dropped — because a SUCCESS changes who this tab is, and
 *  the markers exist to stop a loop for one identity, not to outlive it. */
function forgetSilentTry() {
  try {
    for (const key of Object.keys(sessionStorage)) {
      if (key.startsWith("sg.silenttry:")) sessionStorage.removeItem(key);
    }
  } catch { /* nothing to forget */ }
}

// ── boot ────────────────────────────────────────────────────────────────────

async function boot() {
  // The language first, with its picker: everything after is drawn once, in the
  // right language, instead of flickering through English.
  document.documentElement.lang = LOCALE;
  mountPicker($("lang"), paintStrings);
  // The node's own description first: everything after it is a decision that
  // needs it — where the catalogue is, whether a sign-in is even possible.
  node = await request("GET", "/node").catch(() => null);
  authConfig = await oidc.loadConfig(BASE).catch(() => null);
  paintStrings();

  if (authConfig && oidc.returningFromIdp()) {
    const result = await oidc.completeSignIn(authConfig);
    if (result.ok) {
      adoptSession(result);
      forgetSilentTry();        // the markers belong to the session that failed
    } else if (result.silent) {
      // EXPECTED, not broken: `prompt=none` with no session answers
      // `login_required`. Shouting here would be an error message for something
      // that went exactly as designed — the gate says its own sentence instead.
      note($("gate-note"), "");
    } else {
      note($("gate-note"), t("session.incomplete", { error: result.error }), true);
    }
  } else if (authConfig && authConfig.enforcing && !token && !silentTried()) {
    // ONE SILENT ATTEMPT PER DOOR PER TAB, and then never again.
    //
    // The split by verb cost this: the face used to be one page, so signing in
    // was one click; with three doors and a token that lives in memory, walking
    // between them asked again each time. Measured in Chrome — and it is the
    // property the split exists for that it would have spent.
    //
    // Keyed by the door, because each door is its own `redirect_uri` and a marker
    // shared between them would let one door's refusal silence another's chance.
    markSilentTry();
    await oidc.signIn(authConfig, { silent: true });
    return;                     // navigating; the page comes back either way
  }

  // WHERE TO GO, before anybody knows who you are: the vestibule's whole job.
  // The destinations do not depend on a session — an unsigned visitor is sent to
  // the published, which is exactly what they can have.
  renderDestinations();

  // A room listing needs only that you are SOMEBODY. So the question is simply
  // whether the listing answers — and if it does not, NO EMPTY LIST is shown:
  // "there is nothing here" would be a lie when the truth is "I do not know who
  // you are". That is the mute gate in a more elegant form, which makes it worse.
  // …AND ONLY WHERE THERE IS A LIST TO DRAW. Asking `/rooms` from the tools page
  // earned a 401 for a listing nothing would render — a request made for its side
  // effect, and a 401 in the node's log that means nothing is a 401 somebody will
  // one day chase.
  if (!$("zone-rooms")) {
    // No listing on this door — but «who you are» is this door's actual job, so
    // it is asked here and not inherited from a request about rooms.
    await showWhoYouAre();
    renderDestinations();         // …and the emphasis follows the answer
    await loadNodeMap();          // the operator's door still depends on it
    return;
  }
  let rooms = null;
  try {
    rooms = await request("GET", "/rooms");
  } catch (error) {
    if (error.status === 401 || error.status === 403) { showGate(); return; }
    note($("gate-note"),
         t("gate.unreachable", { base: BASE, error: error.message }), true);
    showGate();
    return;
  }
  await enter(rooms);
}

function showGate() {
  $("gate").hidden = false;
  if ($("zone-rooms")) $("zone-rooms").hidden = true;
  $("btn-signin").hidden = !(authConfig && authConfig.enforcing
                             && authConfig.authorization_endpoint);
  if (!authConfig) {
    note($("gate-note"), t("gate.noOidc"), true);
  } else if (!authConfig.enforcing) {
    // A node that checks nothing is not a node you sign in to: saying "sign in"
    // there would offer a button that cannot work.
    note($("gate-note"), t("gate.devMode")
         + (authConfig.missing?.length
            ? ". " + t("gate.devMode.missing",
                       { what: authConfig.missing.join(" · ") })
            : "."), true);
  }
}

/**
 * WHO YOU ARE — and this belongs to EVERY door, not to the one with a list.
 *
 * The bug it repairs, measured in Chrome with a live session: the vestibule went
 * on saying «Sign in to see the rooms you work in», with no name and no way out,
 * WHILE the node map and the «amministrare» door were on the same screen — both
 * of which only appear for a signed-in operator. A page contradicting itself
 * about whether you are signed in is worse than a page that does not know.
 *
 * The cause was mine: the vestibule stopped asking for the rooms listing (it has
 * no list to draw), and `enter()` — which closed the gate, wrote the name and
 * revealed the sign-out — hung off that listing.
 *
 * And the note says where it belongs: «il vestibolo non possiede niente: compone
 * /v1/node e /v1/whoami». So the identity comes from `/v1/whoami`, which is the
 * endpoint for exactly this question, and the listing stays on the page that
 * shows a listing. Where the two disagreed, the note wins.
 */
async function showWhoYouAre() {
  try { me = await request("GET", "/whoami"); } catch { me = null; }
  const known = Boolean(me && (me.name || me.orcid));
  if ($("gate")) $("gate").hidden = known;
  if ($("who")) {
    $("who").textContent = known
      ? [me.name, me.orcid].filter(Boolean).join(" · ") : "";
  }
  if ($("btn-signout")) $("btn-signout").hidden = !(authConfig && token);
  // …AND THE WAY IN, which is the other half of the same statement. Measured in
  // a browser with no realm session: the vestibule said «sign in to see the
  // rooms you work in» and offered NO button, because the reveal used to hang off
  // `showGate()` and `showGate()` hangs off the rooms listing this door no longer
  // asks for. A gate that names what is behind it and not how to pass is the mute
  // gate this file already has a comment about, in a more elegant form — which
  // makes it worse.
  if ($("btn-signin")) {
    $("btn-signin").hidden = known || !(authConfig && authConfig.enforcing
                                        && authConfig.authorization_endpoint);
  }
  // …and a node that enforces NOTHING says so, instead of showing a button that
  // cannot work — the same sentence `showGate` uses, from the same condition.
  if (!known && authConfig && !authConfig.enforcing && $("gate-note")
      && !$("gate-note").textContent) {
    note($("gate-note"), t("gate.devMode")
         + (authConfig.missing?.length
            ? ". " + t("gate.devMode.missing",
                       { what: authConfig.missing.join(" · ") })
            : "."), true);
  }
  return known;
}

async function enter(rooms) {
  await showWhoYouAre();
  // The create box appears exactly when creating would be ACCEPTED, and no
  // sooner. MEASURED rather than assumed: `POST /v1/rooms` has no role gate —
  // any identity may create one and becomes its owner — so reaching this line
  // (the listing answered) is the same condition. If a gate is ever added, the
  // right shape is a field on `/v1/whoami`, not a guess here: a page that
  // decided for itself would be a page you can talk out of it.
  if ($("create-row")) $("create-row").hidden = false;
  renderRooms(rooms);
  // …and the operator's map, asked for only now: `enter` is reached when the
  // listing answered, i.e. when there IS a session to ask with.
  await loadNodeMap();
}

// ── create ──────────────────────────────────────────────────────────────────

async function createRoom() {
  const input = $("new-room-title");
  const title = (input.value || "").trim();
  if (!title) { note($("create-note"), t("rooms.needsName"), true); return; }
  // The id is derived from the name so a person never types two things that must
  // agree. Collisions are the SERVER's to refuse, and its sentence is shown.
  const room_id = title.toLowerCase().replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "").slice(0, 60) || `room-${Date.now().toString(36)}`;
  try { await request("POST", "/rooms", { room_id, title }); }
  catch (error) { note($("create-note"), error.message, true); return; }
  input.value = "";
  note($("create-note"), t("rooms.created"));
  renderRooms(await request("GET", "/rooms"));
}

// ── wiring ──────────────────────────────────────────────────────────────────

$("btn-signin")?.addEventListener("click", async () => {
  if (!authConfig) { note($("gate-note"), t("gate.noOidc"), true); return; }
  await oidc.signIn(authConfig);
});
// Signing out has to close the REALM's session, not only this tab. One signature
// opens four tools on this origin; an exit that forgot a token here would leave
// the device signed in on the other three, and the rule about a device that
// changes hands would hold for one face out of four — which is not holding.
$("btn-signout")?.addEventListener("click", () => {
  const url = authConfig ? oidc.signOutUrl(authConfig) : "";
  token = ""; refreshToken = ""; window.clearTimeout(refreshTimer);
  if (url) window.location.href = url;
  else window.location.reload();
});
// …and the create box only exists on the work page, so the wiring asks first.
// `?.` and not an `if`: a control that is not on this page is not an error, and
// three scripts to avoid three question marks would be the worse trade.
$("btn-create")?.addEventListener("click", () => void createRoom());
$("new-room-title")?.addEventListener("keydown", (event) => {
  if (event.key === "Enter") void createRoom();
});

await boot();
