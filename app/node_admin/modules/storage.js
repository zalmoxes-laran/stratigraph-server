/**
 * MODULE 2 · Storage — what the node is holding, and what does not line up.
 *
 * Three columns of oversight, and each answers a question an operator cannot ask
 * from inside one room: which rooms point at containers that are gone, how many
 * of the assets a room references the store actually has, and which stored
 * digests **no** room mentions.
 *
 * MinIO is not addressed here. It never is from a browser: the numbers come from
 * `/v1/admin/storage`, which reads the store through the same interface the asset
 * route uses. No bucket listing, no presigned URL, nothing that would make the
 * client a second door to the bytes.
 */
import { api, confirmNamed, escapeHtml, register, say, show } from "../console.js";

async function render(root) {
  const report = await api.get("/admin/storage");
  root.innerHTML = "";

  const head = document.createElement("div");
  head.className = "head";
  head.innerHTML = `<h1>Storage</h1>
    <p class="muted">
      assets: <b>${escapeHtml(report.asset_store)}</b> ·
      snapshots: <b>${escapeHtml(report.snapshot_store)}</b> ·
      room records: <b>${escapeHtml(report.room_store)}</b>
    </p>`;
  root.appendChild(head);

  const table = document.createElement("table");
  table.className = "wide";
  table.innerHTML = `<thead><tr>
      <th>room</th><th>record</th><th>containers</th>
      <th class="num">assets</th><th class="num">present</th><th>state</th><th></th>
    </tr></thead><tbody></tbody>`;
  const body = table.querySelector("tbody");

  for (const room of report.rooms) {
    const row = document.createElement("tr");
    const trouble = room.missing_refs.length || room.missing.length;
    row.className = trouble ? "warn-row" : "";
    row.innerHTML = `
      <td class="mono">${escapeHtml(room.room_id)}</td>
      <td>${room.declared ? "declared" : `<span class="tag warn">implicit</span>`}</td>
      <td class="mono small">${room.missing_refs.length
        ? `<span class="tag bad">missing: ${room.missing_refs.map(escapeHtml).join(", ")}</span>`
        : "ok"}</td>
      <td class="num">${room.assets}</td>
      <td class="num">${room.present}${room.present < room.assets
        ? ` <span class="tag bad">${room.assets - room.present} not in store</span>` : ""}</td>
      <td>${room.archived_at
        ? `<span class="tag">archived</span>` : `<span class="muted">live</span>`}</td>
      <td class="right"></td>`;
    row.querySelector(".right").appendChild(lifecycleButton(room));
    body.appendChild(row);
  }
  root.appendChild(table);

  // ── the orphans ─────────────────────────────────────────────────────────
  const orphans = document.createElement("section");
  orphans.className = "card";
  if (report.orphan_assets.length) {
    orphans.innerHTML = `<h2>Orphan assets
        <span class="tag warn">${report.orphan_assets.length}</span></h2>
      <p class="muted">Digests the store holds that no room's document mentions.
        <b>Named, not deleted</b> — bytes nobody references may still be the
        upload somebody is about to point at, and a sweep that removed them would
        be a policy nobody wrote down.</p>
      <div class="mono small scroll">${report.orphan_assets
        .map((d) => escapeHtml(d)).join("<br>")}</div>`;
  } else {
    orphans.innerHTML = `<h2>Orphan assets</h2>
      <p class="muted">None reported. Note that a store which cannot enumerate
        (MinIO, on purpose — listing a shared bucket on a page load is an
        expensive question) reports none rather than a partial list.</p>`;
  }
  root.appendChild(orphans);
}

function lifecycleButton(room) {
  const button = document.createElement("button");
  button.className = "ghost";
  button.textContent = room.archived_at ? "restore" : "archive";
  button.title = room.missing_refs.length
    ? "This room points at a container the store does not have"
    : "Mark this room archived. It stays listed; nothing is deleted.";
  button.addEventListener("click", async () => {
    const archiving = !room.archived_at;
    if (archiving && !confirmNamed(
      "Archive this room? It stays listed and nothing is deleted.",
      room.room_id)) return;
    try {
      await api.post(`/admin/rooms/${encodeURIComponent(room.room_id)}/archive`,
                     { archived: archiving, confirm_room_id: room.room_id });
      say(`${room.room_id} ${archiving ? "archived" : "restored"}`, "good");
      show(MODULE);
    } catch (error) {
      say(error.message, "bad");
    }
  });
  return button;
}

const MODULE = register({ id: "storage", title: "Storage", render });
