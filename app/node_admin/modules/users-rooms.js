/**
 * MODULE 1 · Users & Rooms — every room on the node, who is in it, and the links
 * that let people in.
 *
 * It calls the operator-scoped API and nothing else: `/v1/admin/rooms` for the
 * reach, and the room's OWN endpoints (`/v1/rooms/{id}/members`, `…/invites`) for
 * the acts — the same endpoints EMStudio's Members panel calls. Two faces, one
 * contract: if a rule changes in `access.py`, both faces change with it because
 * neither of them holds a copy.
 */
import { api, confirmNamed, escapeHtml, register, say, show } from "../console.js";

const ROLES = ["viewer", "editor", "admin", "owner"];

async function render(root) {
  const rooms = await api.get("/admin/rooms");
  root.innerHTML = "";

  const head = document.createElement("div");
  head.className = "head";
  head.innerHTML = `<h1>Users &amp; Rooms</h1>
    <p class="muted">${rooms.length} room(s) on this node.
    A room with no record is <em>implicit</em>: it predates the register and
    nobody has titled or claimed it.</p>`;
  root.appendChild(head);

  if (!rooms.length) {
    say("This node holds no rooms yet.");
    return;
  }

  for (const room of rooms) root.appendChild(card(room));
}

function card(room) {
  const box = document.createElement("section");
  box.className = "card" + (room.archived_at ? " archived" : "");
  const tags = [
    room.implicit ? `<span class="tag warn">implicit</span>` : "",
    room.archived_at ? `<span class="tag">archived ${escapeHtml(room.archived_at)}</span>` : "",
    room.missing_refs?.length
      ? `<span class="tag bad">missing container: ${room.missing_refs.map(escapeHtml).join(", ")}</span>`
      : "",
  ].join(" ");

  box.innerHTML = `
    <h2>${escapeHtml(room.title || room.room_id)} ${tags}</h2>
    <p class="muted mono">${escapeHtml(room.room_id)}
      · containers: ${room.container_refs.map(escapeHtml).join(", ") || "—"}
      · owner: ${escapeHtml(room.owner || "nobody yet")}
      ${room.created_by ? "· created by " + escapeHtml(room.created_by) : ""}
      ${room.created_at ? "· " + escapeHtml(room.created_at) : ""}</p>
    <table class="members"><tbody></tbody></table>
    <div class="row actions"></div>`;

  const body = box.querySelector("tbody");
  const members = room.members || [];
  if (room.owner) {
    body.appendChild(memberRow(room, { orcid: room.owner, role: "owner" }, true));
  }
  for (const member of members) {
    if (member.orcid === room.owner) continue;
    body.appendChild(memberRow(room, member, false));
  }
  if (!room.owner && !members.length) {
    const empty = document.createElement("tr");
    empty.innerHTML = `<td colspan="3" class="muted">Nobody has a grant here yet.
      The first authenticated arrival becomes the owner.</td>`;
    body.appendChild(empty);
  }

  const actions = box.querySelector(".actions");
  actions.appendChild(addMemberForm(room));
  actions.appendChild(inviteButton(room));
  actions.appendChild(archiveButton(room));
  return box;
}

function memberRow(room, member, isOwner) {
  const row = document.createElement("tr");
  row.innerHTML = `<td class="mono">${escapeHtml(member.orcid)}</td>
    <td>${escapeHtml(member.role)}</td><td class="right"></td>`;
  const cell = row.querySelector(".right");
  if (isOwner) {
    // The owner is not editable from a list. `access.may_assign` refuses it
    // server-side too — the UI simply does not offer an action the node would
    // refuse, which is the rule for both faces.
    cell.innerHTML = `<span class="muted">owner · transfer only</span>`;
    return row;
  }
  const select = document.createElement("select");
  for (const role of ROLES.filter((r) => r !== "owner")) {
    const option = document.createElement("option");
    option.value = option.textContent = role;
    if (role === member.role) option.selected = true;
    select.appendChild(option);
  }
  select.addEventListener("change", async () => {
    try {
      await api.put(`/rooms/${encodeURIComponent(room.room_id)}/members/`
                    + encodeURIComponent(member.orcid), { role: select.value });
      say(`${member.orcid} → ${select.value}`, "good");
    } catch (error) {
      // The node's sentence, verbatim: "the owner cannot be demoted; transfer
      // the room first" is the answer, and paraphrasing it would lose it.
      say(error.message, "bad");
      show(MODULE);
    }
  });
  const revoke = document.createElement("button");
  revoke.className = "ghost";
  revoke.textContent = "revoke";
  revoke.addEventListener("click", async () => {
    if (!confirmNamed("Revoke this person's access?",
                      `${member.orcid} in ${room.room_id}`)) return;
    try {
      await api.del(`/rooms/${encodeURIComponent(room.room_id)}/members/`
                    + encodeURIComponent(member.orcid));
      say(`${member.orcid} revoked`, "good");
      show(MODULE);
    } catch (error) {
      say(error.message, "bad");
    }
  });
  cell.append(select, revoke);
  return row;
}

function addMemberForm(room) {
  const form = document.createElement("form");
  form.className = "inline";
  form.innerHTML = `<input name="orcid" placeholder="0000-0000-0000-0000" size="21">
    <select name="role">${ROLES.filter((r) => r !== "owner")
      .map((r) => `<option>${r}</option>`).join("")}</select>
    <button>add</button>`;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const orcid = form.orcid.value.trim();
    if (!orcid) return;
    try {
      await api.put(`/rooms/${encodeURIComponent(room.room_id)}/members/`
                    + encodeURIComponent(orcid), { role: form.role.value });
      say(`${orcid} → ${form.role.value}`, "good");
      show(MODULE);
    } catch (error) {
      say(error.message, "bad");
    }
  });
  return form;
}

function inviteButton(room) {
  const wrap = document.createElement("div");
  wrap.className = "inline";
  const role = document.createElement("select");
  role.innerHTML = `<option>viewer</option><option>editor</option>`;
  const make = document.createElement("button");
  make.textContent = "invite link";
  const out = document.createElement("div");
  out.className = "token";
  make.addEventListener("click", async () => {
    try {
      const invite = await api.post(
        `/rooms/${encodeURIComponent(room.room_id)}/invites`,
        { role: role.value });
      // Shown ONCE, because that is all there is: the node keeps a digest.
      out.innerHTML = `<code>${escapeHtml(invite.token)}</code>`;
      const copy = document.createElement("button");
      copy.className = "ghost";
      copy.textContent = "copy";
      copy.addEventListener("click", () => navigator.clipboard?.writeText(invite.token));
      out.appendChild(copy);
      say(`link for ${invite.role} — shown once, the node keeps only a digest`,
          "good");
    } catch (error) {
      say(error.message, "bad");
    }
  });
  const list = document.createElement("button");
  list.className = "ghost";
  list.textContent = "links";
  list.addEventListener("click", async () => {
    try {
      const invites = await api.get(`/rooms/${encodeURIComponent(room.room_id)}/invites`);
      out.innerHTML = invites.length
        ? invites.map((i) => `<div class="mono small">${escapeHtml(i.token_id)} ·
            ${escapeHtml(i.role)} · ${escapeHtml(i.state)} · used ${i.uses}
            ${i.accepted_by?.length ? "· " + i.accepted_by.map(escapeHtml).join(", ") : ""}
            </div>`).join("")
        : `<span class="muted">no links</span>`;
    } catch (error) {
      say(error.message, "bad");
    }
  });
  wrap.append(role, make, list, out);
  return wrap;
}

function archiveButton(room) {
  const button = document.createElement("button");
  button.className = "ghost";
  button.textContent = room.archived_at ? "restore" : "archive";
  button.addEventListener("click", async () => {
    const archiving = !room.archived_at;
    if (archiving && !confirmNamed("Archive this room?", room.room_id)) return;
    try {
      await api.post(`/admin/rooms/${encodeURIComponent(room.room_id)}/archive`,
                     { archived: archiving, confirm_room_id: room.room_id });
      say(`${room.room_id} ${archiving ? "archived" : "restored"} — marked, not `
          + `deleted`, "good");
      show(MODULE);
    } catch (error) {
      say(error.message, "bad");
    }
  });
  return button;
}

const MODULE = register({ id: "users-rooms", title: "Users & Rooms", render });
