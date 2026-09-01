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
 */
const BASE = new URL(".", window.location.href).pathname
  .replace(/rooms\/$/, "").replace(/\/$/, "") + "/v1";

let token = "";
let refreshToken = "";
let refreshTimer = 0;
let authConfig = null;
let node = null;          // what /v1/node answered: this node's own description
let me = null;
//: the last room listing, kept ONLY so a change of language repaints without
//: a round trip to the node. Never a source of truth: every render replaces it.
let LAST_ROOMS = null;

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

/**
 * A STRING out of a value that may be an entity, and never an object.
 *
 * The `[object Object]` on the monument cards was not a typo: it was `||` doing
 * exactly what it is for, with an object as the last operand. Anything this page
 * puts in a text node goes through here, so the failure mode cannot come back by
 * somebody adding one more fallback — which is how it would come back.
 *
 * `field` picks which part of an entity is wanted: its name to show, its id to
 * cite.
 */
function entityText(value, field = "name") {
  if (value == null) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (typeof value === "object") {
    const picked = value[field] ?? value.name ?? value.iri ?? value.id;
    return typeof picked === "string" ? picked : "";
  }
  return "";
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
  const host = $("rooms");
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

// How many cards a zone shows before it starts saying "and N more". ONE number,
// because two zones truncating at two different counts would be a difference
// nobody decided.
const SHOWN = 8;

// ── zone 2 · the studies — what has been published ──────────────────────────

async function loadStudies(base) {
  let answer;
  try {
    answer = await call(`${base}/studies`);
  } catch (error) {
    // A catalogue this node NAMED and cannot reach is not the same as no
    // catalogue: the second is silent (the zone never opens, see `catalogBase`),
    // the first has to say so. Measured the hard way — on a node where the
    // catalogue sits on another ORIGIN the browser refuses the request for CORS,
    // and the zone vanished with no way to tell that from "no studies yet".
    $("zone-studies").hidden = false;
    $("studies").replaceChildren(el("p", "note warn",
      t("catalog.silent", { base, error: error.message })));
    return;
  }
  // A LIST THAT TRUNCATES SAYS SO, AND COUNTS. Measured on 4 September with
  // real data: 37 studies in the catalogue, eight cards on the door, and Villa
  // di Aiano simply not among them — with nothing on the page to suggest the
  // door was being quiet. A visible debt is a debt somebody pays; a hidden one
  // is a swamp, and the person who fell into this one spent a while looking for
  // a study that was there all along.
  const all = answer.studies || [];
  const studies = all.slice(0, SHOWN);
  if (!studies.length) return;
  $("zone-studies").hidden = false;
  const host = $("studies");
  host.innerHTML = "";
  for (const study of studies) host.append(studyCard(base, study));
  if (all.length > studies.length) {
    host.append(el("p", "note truncated",
      t("studies.someOf", { shown: studies.length, total: all.length })));
  }
  const link = $("all-studies");
  link.href = base;                 // the SERVICE, not a path we invented
  link.hidden = false;
}

function studyCard(base, study) {
  const authors = (study.authors || []).map((a) => a.name).filter(Boolean);
  return card({
    title: study.title || study.id,
    id: study.em_id || "",
    tag: study.visibility || "",
    verb: t("studies.verb"),
    notes: [authors.join(" · "),
            study.embargo_active
              ? t("studies.embargo", { date: study.embargo }) : "",
            study.license_effective || ""],
    build(actions, said, box) {
      const open = el("button", "", t("studies.openIn"));
      open.addEventListener("click", () => void studyDoors(base, study, box, said));
      actions.append(open);
    },
  });
}

/** The doors of a study, ASKED — `<catalog>/study/{id}/open`, the twin of the
 *  rooms' one. Drawn on demand rather than up front because there are up to
 *  sixteen studies on this page and sixteen extra requests to draw a button
 *  nobody may press is a page that is slow for everybody. */
async function studyDoors(base, study, box, said) {
  let doors;
  try { doors = await call(`${base}/study/${encodeURIComponent(study.id)}/open`); }
  catch (error) { note(said, error.message, true); return; }
  const row = box.querySelector(".room-actions");
  for (const [tool, target] of Object.entries(doors.apps || {})) {
    if (box.querySelector(`.tool[data-tool="${tool}"]`)) continue;
    const openers = [];
    if (target.scheme) {
      // `followScheme`, the SAME mechanism a room's door uses: on a machine with
      // no handler this said nothing whatsoever, while the room next to it said
      // «Nothing opened». Measured on 4 September, and it is the second half of
      // the same silence — the link itself named no catalogue, so even where a
      // handler existed there was nothing to resolve.
      openers.push([t("door.desktop"), t("door.desktop.title", { tool }),
                    () => followScheme(target.scheme, box, said)]);
    }
    if (target.web) {
      openers.push([t("door.browser"),
                    t("door.browser.title", { tool, url: target.web }),
                    () => window.open(target.web, "_blank", "noopener")]);
    }
    if (target.emjson) {
      openers.push([t("door.emjson"), t("door.emjson.title"),
                    () => window.open(target.emjson, "_blank", "noopener")]);
    }
    if (!openers.length) continue;      // a tool with no door draws none
    const group = toolGroup(tool, openers);
    group.dataset.tool = tool;
    row.insertBefore(group, said);
  }
}

// ── zone 3 · the monuments — the subject, which endures ─────────────────────
//
// Same card, third source, third verb. The grouping is the CATALOGUE's
// (`?view=hdt` → `group_by_hdt`): doing it here would be a second implementation
// of an identity rule that already has one, and the one here would be the one
// nobody tested.

async function loadMonuments(base) {
  let answer;
  // Silent here, and only here: `loadStudies` ran first against the same
  // service and has already said whatever there was to say. Two identical
  // complaints about one catalogue is noise.
  try { answer = await call(`${base}/studies?view=hdt`); }
  catch { return; }
  // The group WITHOUT an HDT is dropped, and that is presentation and not
  // visibility: `group_by_hdt` deliberately keeps studies with no digital twin
  // under a `None` key ("which of my studies have no twin yet" is a curator's
  // question), and that bucket is not a monument. It is answered in the
  // catalogue, which is where a curator is.
  const groups = (answer.groups || []).filter((g) => g.hdt || g.hc2);
  if (!groups.length) return;
  $("zone-hdt").hidden = false;
  const host = $("hdt");
  host.innerHTML = "";
  const shown = groups.slice(0, SHOWN);
  for (const group of shown) host.append(monumentCard(base, group));
  if (groups.length > shown.length) {
    host.append(el("p", "note truncated",
      t("hdt.someOf", { shown: shown.length, total: groups.length })));
  }
}

function monumentCard(base, group) {
  const studies = group.studies || [];
  const count = studies.length;
  return card({
    // `label` comes from the CATALOGUE now (`app/index.py::group_label`), which
    // is the side that has the name. This chain used to end at `group.hc2` —
    // an ENTITY, `{id, name, iri}` — and printed `[object Object]` on the
    // monument cards the moment real data arrived. A fallback that can be an
    // object is not a fallback; `entityText` refuses to return one.
    title: group.label || entityText(group.hc2) || entityText(group.hc1)
           || t("hdt.unnamed"),
    id: entityText(group.hc2, "id") || entityText(group.hc1, "id") || "",
    verb: t("hdt.verb"),
    notes: [t("hdt.studies" + (count === 1 ? ".one" : ".many"), { n: count })],
    build(actions, said, box) {
      const show = el("button", "", t("hdt.itsStudies"));
      show.addEventListener("click", () => {
        if (box.querySelector(".sub-list")) { box.querySelector(".sub-list").remove(); return; }
        const list = el("div", "sub-list");
        for (const study of studies) list.append(studyCard(base, study));
        box.append(list);
      });
      actions.append(show);
    },
  });
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
  // `/admin/whoami` answers WITHOUT a 403 — that is why the console asks it
  // before drawing anything — so this is a question, not an attempt.
  let who = null;
  try { who = await request("GET", "/admin/whoami"); } catch { return; }
  if (!who || who.operator !== true) return;

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
      // …and a public entrance is reachable from wherever the operator is
      fromNode: false,
    }));
  }

  const neighbours = $("map-neighbours");
  neighbours.innerHTML = "";
  for (const check of report.checks || []) {
    neighbours.append(mapRow({
      label: check.name, state: check.state, detail: check.detail,
      browser: check.browser, internal: check.probe || check.target,
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
function mapRow({ label, state, detail, browser, internal, facts, fromNode }) {
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
    if (/^https?:\/\//.test(internal)) {
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
  set("studies-title", t("studies.title"));
  set("studies-sub", t("studies.sub"));
  set("hdt-title", t("hdt.title"));
  set("hdt-sub", t("hdt.sub"));
  set("map-title", t("map.title"));
  set("map-sub", t("map.sub"));
  set("map-entrances-head", t("map.entrances"));
  set("map-neighbours-head", t("map.neighbours"));
  set("btn-create", t("rooms.create"));
  set("all-studies", t("studies.all"));
  const input = $("new-room-title");
  if (input) input.placeholder = t("rooms.newName");
  renderNodeLine();
  renderServices();
  // …and the three lists, from what they are already holding: a language change
  // must not cost a round trip to the node.
  if (LAST_ROOMS) renderRooms(LAST_ROOMS);
  const catalog = catalogBase();
  if (catalog && !$("zone-studies").hidden) { void loadStudies(catalog); void loadMonuments(catalog); }
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
    if (result.ok) adoptSession(result);
    else note($("gate-note"), t("session.incomplete", { error: result.error }), true);
  }

  // The catalogue answers anonymous callers with the PUBLIC studies — not an
  // empty list and not a 401 — so these two zones are honest before a sign-in.
  const catalog = catalogBase();
  if (catalog) { await loadStudies(catalog); await loadMonuments(catalog); }

  // A room listing needs only that you are SOMEBODY. So the question is simply
  // whether the listing answers — and if it does not, NO EMPTY LIST is shown:
  // "there is nothing here" would be a lie when the truth is "I do not know who
  // you are". That is the mute gate in a more elegant form, which makes it worse.
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
  $("zone-rooms").hidden = true;
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

async function enter(rooms) {
  $("gate").hidden = true;
  try { me = await request("GET", "/whoami").catch(() => null); } catch { me = null; }
  $("who").textContent = (me && (me.name || me.orcid))
    ? [me.name, me.orcid].filter(Boolean).join(" · ") : "";
  $("btn-signout").hidden = !(authConfig && token);
  // The create box appears exactly when creating would be ACCEPTED, and no
  // sooner. MEASURED rather than assumed: `POST /v1/rooms` has no role gate —
  // any identity may create one and becomes its owner — so reaching this line
  // (the listing answered) is the same condition. If a gate is ever added, the
  // right shape is a field on `/v1/whoami`, not a guess here: a page that
  // decided for itself would be a page you can talk out of it.
  $("create-row").hidden = false;
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

$("btn-signin").addEventListener("click", async () => {
  if (!authConfig) { note($("gate-note"), t("gate.noOidc"), true); return; }
  await oidc.signIn(authConfig);
});
// Signing out has to close the REALM's session, not only this tab. One signature
// opens four tools on this origin; an exit that forgot a token here would leave
// the device signed in on the other three, and the rule about a device that
// changes hands would hold for one face out of four — which is not holding.
$("btn-signout").addEventListener("click", () => {
  const url = authConfig ? oidc.signOutUrl(authConfig) : "";
  token = ""; refreshToken = ""; window.clearTimeout(refreshTimer);
  if (url) window.location.href = url;
  else window.location.reload();
});
$("btn-create").addEventListener("click", () => void createRoom());
$("new-room-title").addEventListener("keydown", (event) => {
  if (event.key === "Enter") void createRoom();
});

await boot();
