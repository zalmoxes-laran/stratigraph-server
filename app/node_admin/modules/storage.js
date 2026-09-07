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
import { api, confirmNamed, confirmTyped, escapeHtml, register, say, show }
  from "../console.js";

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
    // ── COUNTED, AND NOW REMOVABLE — one at a time, by a person ─────────────
    //
    // Until 8 October 2026 this list said «Named, not deleted», and that half
    // was right: no sweep, no timer. But counting a thing nobody can touch is
    // the surest way to teach people not to look at the number — and there was
    // nowhere else to touch it except inside MinIO, by hand.
    //
    // So each digest gets its own button, and it asks the node rather than the
    // store: `DELETE /v1/admin/assets/{ref}` re-checks who references those
    // bytes AT THE MOMENT OF ASKING and refuses with the list. This report may
    // be a minute old; the refusal is not.
    //
    // `storage.js`'s rule stands: MinIO is not addressed from here. No bucket
    // listing, no presigned URL, no second door to the bytes.
    orphans.innerHTML = `<h2>Orphan assets
        <span class="tag warn">${report.orphan_assets.length}</span></h2>
      <p class="muted">Digests the store holds that no room's document mentions.
        <b>No sweep and no timer</b> — one at a time, named by a person: bytes
        nobody references may still be the upload somebody is about to point at.
        The node re-checks who references them when you ask, and refuses if
        anybody does.</p>
      <div class="orphan-rows"></div>`;
    const rows = orphans.querySelector(".orphan-rows");
    for (const digest of report.orphan_assets) {
      rows.appendChild(orphanRow(digest));
    }
  } else {
    // «NONE» AND «CANNOT SAY» ARE TWO DIFFERENT ANSWERS, and until 8 October
    // 2026 this branch collapsed them: NO implementation could enumerate, so
    // the list was always empty and the sentence blamed MinIO — true, and
    // incomplete. The two local stores answer now; MinIO still does not, and
    // that is a decision (a bucket listing on a page load is an expensive
    // question, and `storage.js`'s rule stands either way).
    const muto = /minio|s3/i.test(String(report.asset_store || ""));
    orphans.innerHTML = `<h2>Orphan assets</h2>
      <p class="muted">${muto
        ? `This store does not enumerate (<b>${escapeHtml(report.asset_store)}</b>,
           on purpose — listing a shared bucket on a page load is an expensive
           question), so it reports <b>none rather than a partial list</b>. An
           orphan here can still be forgotten: name its digest to the node.`
        : `<b>None.</b> Every digest
           <b>${escapeHtml(report.asset_store)}</b> holds is mentioned by a
           room's document.`}</p>`;
  }
  root.appendChild(orphans);
}

/** One orphan, and the verb that removes it.
 *
 *  `confirmTyped` and NOT `confirmNamed`: the second is a `window.confirm` and
 *  is right for its three reversible uses — revoke, archive, restore — and
 *  wrong here. These bytes do not come back, so the digest has to be TYPED. A
 *  dialog dismissed by reflex protects nothing, and typing 64 hex characters is
 *  the one gesture that cannot be done by reflex. (Decided 2026-10-07; there is
 *  no third confirmation.)
 */
function orphanRow(digest) {
  const row = document.createElement("div");
  row.className = "orphan-row";
  const name = document.createElement("span");
  name.className = "mono small";
  name.textContent = digest;
  const drop = document.createElement("button");
  drop.className = "ghost";
  drop.textContent = "forget";
  drop.title = "Remove these bytes from the store. This cannot be undone, and "
    + "the node refuses if any room or the resident corpus still references them.";
  drop.addEventListener("click", async () => {
    if (!confirmTyped("Forget these bytes? They do not come back.", digest)) return;
    try {
      const done = await api.del(`/admin/assets/${encodeURIComponent(digest)}`);
      // THE NODE'S OWN TWO ANSWERS, kept apart. «removed» and «there was
      // nothing to remove» are two different reports for an operator, and
      // collapsing them into "done" would throw away the useful one.
      say(done.removed ? `forgotten: ${digest}`
                       : `nothing to remove: the store did not have ${digest}`,
          done.removed ? "good" : "warn");
      show(MODULE);
    } catch (error) {
      // …and a REFUSAL is the node's sentence, which names the rooms. Rewriting
      // it as "failed" would throw away the remedy.
      say(error.message, "bad");
    }
  });
  row.append(name, drop);
  return row;
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
