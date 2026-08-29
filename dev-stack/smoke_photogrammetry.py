#!/usr/bin/env python3
"""The processing connector, end to end, against a REAL NodeODM.

    python dev-stack/smoke_photogrammetry.py --images <a folder of photographs>

What the unit suites cannot prove is exactly what this measures: that the three
pinned endpoints of a real `opendronemap/nodeodm` behave the way the client
assumes, and that a cluster of photographs that entered through the ordinary
paths (assets + room operations) comes back as a model with its provenance.

The walk:

1. photographs → the room's object store (`PUT /v1/rooms/{room}/asset`),
   content-addressed, exactly as `ingest_photos` puts them there;
2. a graph → the room, over the WebSocket the ecosystem speaks (ADR-002): one
   unit, one acquisition, one resource per photograph;
3. `POST /v1/photogrammetry` → a 202 and a job id;
4. poll until it is terminal, or until `--wait` runs out.

**Running out of `--wait` is NOT a failure of the connector** and is reported as
its own outcome: a reconstruction is minutes on four cores, and the task is still
on the engine with a uuid this script prints. What is checked either way is the
half that must be right immediately — the gate, the staging, the 202, and that
the engine ACCEPTED the task.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from smoke_common import (Tally, alive, body_of, call, detail_of, orcid_of,  # noqa: E402
                          token_for, unique)

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}


async def _seed(base_ws: str, room: str, token: str, ops: list) -> int:
    """Write the graph the ordinary way: operations over the room's socket."""
    import websockets

    applied = 0
    url = f"{base_ws}/v1/rooms/{room}/ws?token={token}"
    async with websockets.connect(url, max_size=None) as socket:
        for op in ops:
            await socket.send(json.dumps({"v": 2, "type": "op", "payload": op}))
            applied += 1
        # let the server settle the writes before we ask it to read them
        await asyncio.sleep(0.5)
    return applied


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8000/v1")
    parser.add_argument("--images", required=True,
                        help="a folder of photographs of ONE thing")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--wait", type=float, default=900.0,
                        help="seconds to poll before reporting 'still running'")
    args = parser.parse_args()

    base = args.base.rstrip("/")
    tally = Tally()
    if not alive(base):
        print(f"no server at {base} — start the dev stack (./fcn-up.sh)")
        return 2

    owner = token_for()
    who = orcid_of(owner)
    folder = pathlib.Path(args.images).expanduser()
    photos = sorted(p for p in folder.iterdir()
                    if p.suffix.lower() in IMAGE_SUFFIXES)[:args.limit]
    if len(photos) < 3:
        print(f"{folder} holds {len(photos)} photograph(s): a reconstruction "
              f"needs at least three")
        return 2

    room = unique("photogrammetry")
    print(f"\n0 · a room, and {len(photos)} photographs · {who}")
    status, _, raw = call("POST", f"{base}/rooms", token=owner,
                          json_body={"room_id": room,
                                     "title": "Smoke · fotogrammetria"})
    tally.ok(status in (200, 201), f"room {room} created", detail_of(raw))

    # ── 1 · the bytes ────────────────────────────────────────────────────────
    print("\n1 · the photographs go into the room's store")
    nodes, edges = [], []
    nodes.append({"id": "US1", "node_type": "US", "name": "US 1"})
    nodes.append({"id": "acq.smoke", "node_type": "dtc_acquisition",
                  "name": "Smoke cluster", "data": {"dtc_kind": "ingest"}})
    for photo in photos:
        blob = photo.read_bytes()
        status, _, raw = call("PUT", f"{base}/rooms/{room}/asset?media_type=image/jpeg",
                              token=owner, data=blob, media_type="image/jpeg")
        if status != 200:
            tally.ok(False, f"{photo.name} stored", detail_of(raw))
            return tally.report("photogrammetry")
        info = body_of(raw)
        rid = f"res.{photo.name}"
        nodes.append({"id": rid, "node_type": "resource", "name": photo.name,
                      "data": {"checksum": info["ref"], "media_type": "image/jpeg",
                               "url": f"asset/{info['ref']}"}})
        edges.append({"id": f"acq__out__{photo.name}", "source": "acq.smoke",
                      "target": rid, "edge_type": "dtc_had_output"})
    tally.ok(True, f"{len(photos)} photographs content-addressed in the store")

    # ── 2 · the graph, over the wire the ecosystem speaks ────────────────────
    print("\n2 · the cluster is recorded in the room (ADR-002 operations)")
    ops = ([{"op": "add_node", "id": n["id"], "node": n} for n in nodes]
           + [{"op": "add_edge", "id": e["id"], "source": e["source"],
               "target": e["target"], "edge_type": e["edge_type"]} for e in edges])
    ws_base = base.replace("https://", "wss://").replace("http://", "ws://")
    ws_base = ws_base.rsplit("/v1", 1)[0]
    try:
        sent = asyncio.run(_seed(ws_base, room, owner, ops))
    except Exception as exc:
        tally.ok(False, "the graph was seeded over the room socket", str(exc))
        return tally.report("photogrammetry")
    tally.ok(sent == len(ops), f"{sent} operations written to the room")

    # ── 3 · the ask ──────────────────────────────────────────────────────────
    print("\n3 · POST /v1/photogrammetry — against the real engine")
    started = time.time()
    status, _, raw = call("POST", f"{base}/photogrammetry", token=owner,
                          json_body={"room_id": room, "cluster": "acq.smoke",
                                     "subject": "US1", "mode": "local"})
    tally.ok(status == 202, "202 with a job id", detail_of(raw))
    if status != 202:
        return tally.report("photogrammetry")
    job = body_of(raw)
    tally.ok(job.get("image_count") == len(photos),
             f"the node staged all {len(photos)} photographs "
             f"(it says {job.get('image_count')})")
    print(f"    job {job['job_id']}")

    # ── 4 · the engine ───────────────────────────────────────────────────────
    print(f"\n4 · polling (up to {int(args.wait)}s)")
    accepted = False
    last = job
    while time.time() - started < args.wait:
        status, _, raw = call("GET", f"{base}/photogrammetry/{job['job_id']}",
                              token=owner)
        if status != 200:
            tally.ok(False, "the job can be polled", detail_of(raw))
            return tally.report("photogrammetry")
        last = body_of(raw)
        if last.get("task_uuid") and not accepted:
            accepted = True
            tally.ok(True, f"the REAL engine accepted the task "
                           f"({last['task_uuid']})")
        if last["status"] in ("done", "failed"):
            break
        print(f"    {int(time.time() - started):4d}s  {last['status']:<8} "
              f"{last.get('progress', 0):5.1f}%  {last.get('detail', '')}")
        time.sleep(10)

    tally.ok(accepted, "the task reached the engine (a uuid came back)",
             "no task_uuid: the client never got past POST /task/new")

    if last["status"] == "done":
        print("\n5 · what appeared in the graph")
        result = last.get("result") or {}
        tally.ok(bool(result.get("model_id")), f"a model: {result.get('model_id')}")
        tally.ok(bool(result.get("transform_id")),
                 f"a placement: {result.get('transform_id')}")
        tally.ok(bool(result.get("process_id")),
                 f"a genesis (crmdig:D7): {result.get('process_id')}")
        roles = {o["role"]: o for o in (result.get("outputs") or [])}
        tally.ok("model" in roles, f"the produced bytes are in the store: "
                                   f"{roles.get('model', {}).get('sha256', '')[:26]}…")
        for output in roles.values():
            status, _, _raw = call("GET",
                                   f"{base}/rooms/{room}/asset/{output['sha256']}",
                                   token=owner)
            tally.ok(status == 200, f"{output['role']} is fetchable by its digest")
        for warning in result.get("warnings") or []:
            print(f"    · declared: {warning}")
    elif last["status"] == "failed":
        print(f"\n5 · the run FAILED: {last.get('error')}")
        tally.ok(False, "the reconstruction finished", str(last.get("error")))
    else:
        print(f"\n5 · STILL RUNNING after {int(args.wait)}s — NOT a failure.")
        print(f"    task {last.get('task_uuid')} on the engine, job "
              f"{job['job_id']} on the node. Poll it:")
        print(f"    curl -H \"Authorization: Bearer $(./dev-stack/token.sh)\" "
              f"{base}/photogrammetry/{job['job_id']}")
    return tally.report("photogrammetry")


if __name__ == "__main__":
    raise SystemExit(main())
