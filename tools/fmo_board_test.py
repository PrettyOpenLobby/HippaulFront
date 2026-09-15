#!/usr/bin/env python3
"""fmo_board_test.py -- the FMO City Control board (services/boardfmo.py) and
its place in the polboards service.

    python tools/fmo_board_test.py

Pins: the nineteen cities and the score exactly as fmowar counts them; the
board NEVER writes game data (every file in the data dir hashes the same after
all of it, and a phase past its judgement is NOT judged by a page view); the
missing-file case; the recent-battles ledger; the connection marker; the
routes and the art whitelist; the Discord message, edited in place.
"""
import ast
import contextlib
import hashlib
import io
import json
import os
import socket
import struct
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, "services"))

CHECKS = []


def check(label, cond, detail=""):
    CHECKS.append(label)
    print("  %-72s %s%s" % (label, "PASS" if cond else "FAIL",
                            ("  " + str(detail)) if detail and not cond else ""), flush=True)
    if not cond:
        raise AssertionError(label)


def tree_hash(d):
    """Every file under d -> its sha256 (the game data the board must not touch)."""
    out = {}
    for root, _dirs, files in os.walk(d):
        for n in files:
            p = os.path.join(root, n)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, d)] = hashlib.sha256(fh.read()).hexdigest()
    return out


class FakeNet:
    """Stands in for urllib.request.urlopen: records requests, answers from a script."""

    def __init__(self):
        self.calls, self.script = [], []

    def __call__(self, req, timeout=None):
        self.calls.append((req.get_method(), req.full_url, req.data or b""))
        st, data = self.script.pop(0) if self.script else (200, {})
        raw = json.dumps(data).encode()
        if st >= 400:
            raise urllib.error.HTTPError(req.full_url, st, "x", {}, _Body(raw))
        return _Resp(st, raw)


class _Body:
    def __init__(self, raw):
        self.raw = raw

    def read(self, *a):
        return self.raw

    def close(self):
        pass


class _Resp(_Body):
    def __init__(self, st, raw):
        super().__init__(raw)
        self.status = st

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fresh(boardfmo):
    boardfmo._SNAP.update(t=0.0, snap=None)
    boardfmo._RENDER.update(sig=None, png=None)


def main():
    tmp = tempfile.mkdtemp(prefix="boardfmo-")
    data = os.path.join(tmp, "data")
    os.makedirs(data)
    os.environ["POL_DATA_DIR"] = data
    os.environ.pop("FMO_WAR_STATE", None)
    os.environ.pop("FMO_SECTOR_WINS", None)
    import fmowar
    import boardfmo
    import polboards
    war_file = os.path.join(data, "fmowar.json")
    mid = fmowar._ts(2026, 10, 1)

    print("no war file yet")
    s = boardfmo.snapshot(now=mid)
    check("the page still has all nineteen cities, every one Deadlock",
          len(s["cities"]) == 19 and all(c["nation"] == 0 for c in s["cities"]))
    check("...a 0 : 0 score out of 66", (s["score"]["ocu"], s["score"]["usn"], s["score"]["total"]) == (0, 0, 66))
    check("...and says there is no war state on file",
          not s["war"]["present"] and boardfmo.status_text(s).startswith("No war state on file"))
    check("...without creating the file by looking", not os.path.exists(war_file))
    check("no ledger and no marker: no battles, no connection claim",
          s["recent"] == [] and s["connections"] is None)

    print("the war as fmo writes it")
    w = fmowar.War(path=war_file)
    fake = {100: {60126: 0}, 200: {69118: 0}, 505: {85102: 0, 87101: 0},
            509: {94099: 0, 94101: 0, 103100: 0}, 513: {112101: 0}}
    w.seed_from_sectors(fake, force=True)
    s = boardfmo.snapshot(now=mid)
    check("a freshly seeded map: the cities on file are Deadlock, nothing changed hands",
          s["war"]["present"] and s["war"]["changed"] == 0
          and all(c["nation"] == 0 for c in s["cities"])
          and boardfmo.status_text(s) == "No sector has changed hands this phase")
    w.settle(69118, fmowar.OCU, won=True, now=mid)          # prod's shape: a win that only fills the counter
    s = boardfmo.snapshot(now=mid)
    check("a win that only fills a counter is NOT a change of hands (prod, 2026-09-12)",
          w.data["sectors"]["69118"]["updated"] and s["war"]["changed"] == 0
          and boardfmo.status_text(s) == "No sector has changed hands this phase")
    for _ in range(3):
        w.settle(85102, fmowar.OCU, won=True, now=mid)      # Maltaf 4 -> O.C.U.
        w.settle(112101, fmowar.USN, won=True, now=mid)     # Peseta 4 -> U.S.N.
        w.settle(94101, fmowar.OCU, won=True, now=mid)      # Oak Hills 3 -> O.C.U.
    w.data["sectors"]["85102"]["bg_max"], w.data["sectors"]["85102"]["bg_min"] = 6, -2
    w.save()
    before = tree_hash(data)
    mtime = os.path.getmtime(war_file)
    s = boardfmo.snapshot(now=mid)
    by = {c["name"]: c for c in s["cities"]}
    check("the score is fmowar's own count (O.C.U. 7 : 4 U.S.N.)",
          (s["score"]["ocu"], s["score"]["usn"]) == (7, 4)
          and fmowar.War(path=war_file, autosave=False).score() == {1: 7, 2: 4})
    check("Maltaf is O.C.U. at one rate step, Peseta U.S.N., Vienne still Deadlock",
          by["Maltaf"]["nation"] == 1 and by["Maltaf"]["control"] == fmowar.RATE_STEP
          and by["Peseta"]["nation"] == 2 and by["Vienne"]["nation"] == 0)
    check("the rows are in the phase page's order, with SE's sector notation",
          [c["name"] for c in s["cities"][:3]] == ["Maltaf", "Oak Hills", "Vienne"]
          and boardfmo.cells(by["Maltaf"]) == {"name": "Maltaf", "sector": "FZ-06 / 12",
                                               "control": "O.C.U.", "rate": "%d%%" % fmowar.RATE_STEP,
                                               "bg": "6 / -2", "rank": "4"},
          boardfmo.cells(by["Maltaf"]))
    check("a Deadlock row shows no rate", boardfmo.cells(by["Vienne"])["rate"] == "-"
          and boardfmo.cells(by["Vienne"])["control"] == "Deadlock")
    check("status counts the held cities once anything moved",
          boardfmo.status_text(s) == "3 of 19 cities held", boardfmo.status_text(s))
    check("the defaults the page labels are fmowar's knobs",
          s["defaults"] == {"counter_cap": fmowar.COUNTER_CAP, "rate_step": fmowar.RATE_STEP})

    print("the board never writes game data")
    late = fmowar._ts(2026, 11, 2)                          # past phase 1's judgement
    s_late = boardfmo.snapshot(now=late)
    check("a page view past the judgement does NOT judge the phase (tick() is never called)",
          s_late["phase"]["ceasefire"] and s_late["phase"]["judged"] is None
          and json.load(open(war_file))["phases"] == {})
    check("...and never inserts a sector it does not have",
          len(json.load(open(war_file))["sectors"]) == len(w.data["sectors"]))
    check("every file in the data dir is byte-identical after the snapshots",
          tree_hash(data) == before and os.path.getmtime(war_file) == mtime)

    print("the phase clock")
    p = s["phase"]
    check("phase 1, judged 2026-11-01 12:00 UTC (fmowar.phase_at)",
          p["n"] == 1 and p["judge"] == fmowar._ts(2026, 11, 1, 12) and not p["ceasefire"])
    check("the clock line says so",
          boardfmo.clock_text(p) == "Phase 1   Judged 2026/11/01 12:00 UTC", boardfmo.clock_text(p))
    check("the ceasefire and the time before the war read as such",
          boardfmo.clock_text(s_late["phase"]).startswith("Phase 1 ceasefire   Phase 2 begins 2026/11/05")
          and boardfmo.clock_text(boardfmo.phase(w, fmowar._ts(2026, 9, 1))).startswith("No war yet"))
    judged = fmowar.War(path=os.devnull, autosave=False)
    judged.data = json.loads(json.dumps(w.data))
    judged.tick(now=late)                                   # in MEMORY, for the event test
    check("a judged phase is read from the file's own record",
          boardfmo.phase(judged, late)["judged"]["winner"] == fmowar.OCU)

    print("names and sectors")
    check("zone labels: FZ-06 / FZ-10 / FZ-14, and the named zones",
          [boardfmo.zone_label(z) for z in (505, 509, 513, 200, 301)]
          == ["FZ-06", "FZ-10", "FZ-14", "O.C.U. Occupied Zone 01", "U.S.N. Control Zone 02"])
    check("sector numbers from fmosectors: both Freedom City fortresses (02 and 66)",
          boardfmo.sector_row(509, 94099) == 2 and boardfmo.sector_row(509, 103100) == 66
          and boardfmo.sector_row(509, 1) is None)

    print("recent battles and connections")
    now = time.time()
    with open(os.path.join(data, "fmo_sector_wins.json"), "w") as fh:
        json.dump({"505:85102:1": [int(now) - 300, int(now) - 60],
                   "200:69118:2": [int(now) - 120], "bad": [1], "509:94101:x": [2]}, fh)
    rb = boardfmo.recent_battles()
    check("newest first, across sectors; bad keys skipped",
          [(b["area"], b["sector"], b["nation"]) for b in rb]
          == [("FZ-06", 12, 1), ("O.C.U. Occupied Zone 01", 1, 2), ("FZ-06", 12, 1)], rb)
    check("a frontline city carries its name, other sectors none",
          rb[0]["city"] == "Maltaf" and rb[1]["city"] == "")
    marker = os.path.join(data, boardfmo.LIVE_MARKER)
    with open(marker, "w") as fh:
        json.dump({"count": 3, "stamp": now}, fh)
    check("a fresh marker = its connection count", boardfmo.connections(now) == 3)
    with open(marker, "w") as fh:
        json.dump({"count": 3, "stamp": now - boardfmo.LIVE_GRACE_S - 5}, fh)
    check("...a stale one = 0", boardfmo.connections(now) == 0)

    print("the page and the image")
    cols = boardfmo.LAYOUT["cols"]
    check("one column model: x ascending, each column one alignment",
          [c["x"] for c in cols] == sorted(c["x"] for c in cols)
          and all(c["align"] in ("left", "right") for c in cols))
    check("the page carries that model", json.dumps(boardfmo.LAYOUT, separators=(",", ":")) in boardfmo.PAGE
          and "/*LAYOUT*/" not in boardfmo.PAGE)
    check("no em dashes in the page", chr(0x2014) not in boardfmo.PAGE)
    check("no SE-numbers disclaimer on the board (decided on the private deployment: the docs' job)",
          "never published" not in boardfmo.PAGE and "note" not in boardfmo.snapshot()
          and "never published" not in json.dumps(boardfmo.discord_message(boardfmo.snapshot())[0]))
    fresh(boardfmo)
    png = boardfmo.render(boardfmo.snapshot())
    if png is None:
        print("  (Pillow or the art is missing -- render checks skipped)")
    else:
        check("render.png is the 800x600 window",
              png[:8] == b"\x89PNG\r\n\x1a\n" and struct.unpack(">II", png[16:24]) == (800, 600))
    served = boardfmo.art_files()
    # backdrop.png is baked from the player's own client (tools/fmo_boardart_bake.py)
    # and is not tracked, so a fresh checkout has the fonts but not the backdrop
    if "backdrop.png" not in served:
        print("  (backdrop.png not baked yet: tools/fmo_boardart_bake.py --client <install>)")
    fonts = {"fmo-text.woff2", "fmo-text-bold.woff2", "fmo-title.woff2"}
    check("the art whitelist: backdrop and web fonts only, no .ttf or licence",
          fonts <= served <= fonts | {"backdrop.png"}
          and not [n for n in served if n.endswith((".ttf", ".txt"))], sorted(served))

    print("Discord: one message, edited in place")
    hook = "https://discord.com/api/webhooks/123/SECRETTOKEN"
    state = os.path.join(tmp, "state", "fmo_discord.json")
    args = polboards.build_parser().parse_args(["--fmo-port", "1", "--fmo-url", "https://fmo.example"])
    fresh(boardfmo)
    snap = boardfmo.snapshot(args)
    payload, files = boardfmo.discord_message(snap, args)
    e = payload["embeds"][0]
    check("the message: score, holders, the newest battles, linked to the page",
          "O.C.U. 7 : 4 U.S.N." in e["description"] and "Maltaf 4" in e["description"]
          and "Recent battles" in e["description"] and e["url"] == "https://fmo.example",
          e["description"])
    if png is not None:
        check("...with the window as its image",
              files and files[0][0] == "fmo-city-control.png" and files[0][2][:4] == b"\x89PNG"
              and e["image"]["url"] == "attachment://fmo-city-control.png")
    check("no em dashes in anything posted", chr(0x2014) not in json.dumps(payload, ensure_ascii=False))
    net = FakeNet()
    d = polboards.Discord("fmo", hook, state, every=60, ttl=600, refresh=1800, opener=net)
    build = lambda: boardfmo.discord_message(snap, args)        # noqa: E731
    net.script = [(200, {"id": "777"})]
    check("the first tick POSTs and keeps the id",
          d.tick("s1", build, now=1000.0) == "posted" and d.msg_id == "777")
    check("...across a restart", polboards.Discord("fmo", hook, state, opener=net).msg_id == "777")
    check("a changed board is EDITED in place",
          d.tick("s2", build, now=1100.0) == "edited" and net.calls[-1][0] == "PATCH"
          and net.calls[-1][1].endswith("/messages/777"))
    out = io.StringIO()
    net.script = [(500, {"message": "boom"})]
    with contextlib.redirect_stdout(out):
        d.tick("s3", build, now=9000.0)
    check("a failure is logged WITHOUT the webhook's secret",
          "failed" in out.getvalue() and "SECRETTOKEN" not in out.getvalue(), out.getvalue())
    s_a = json.loads(json.dumps(snap))
    s_b = json.loads(json.dumps(snap))
    s_b["cities"][2]["nation"] = 2                           # Vienne -> U.S.N.
    s_b["cities"][0]["nation"] = 0                           # Maltaf -> Deadlock
    ev = boardfmo.discord_events(s_a, s_b)
    check("a city changing hands is news, both ways",
          len(ev) == 2 and "Vienne" in ev[1] and "U.S.N." in ev[1]
          and "Maltaf" in ev[0] and "Deadlock" in ev[0], ev)
    s_c = json.loads(json.dumps(snap))
    s_c["phase"]["judged"] = {"ocu": 7, "usn": 4, "winner": 1}
    s_c["phase"]["ceasefire"] = True
    s_d = json.loads(json.dumps(s_c))
    s_d["phase"]["n"] += 1
    s_d["phase"]["judged"] = None
    check("a judged phase and a new phase are news",
          boardfmo.discord_events(snap, s_c) == ["\U0001F3C1 Phase 1 went to **O.C.U.**, O.C.U. 7 : 4 U.S.N."]
          and boardfmo.discord_events(s_c, s_d) == ["\U0001F4E3 Phase 2 has begun"],
          (boardfmo.discord_events(snap, s_c), boardfmo.discord_events(s_c, s_d)))
    check("an unchanged board is no news", boardfmo.discord_events(snap, snap) == [])
    fresh(boardfmo)
    net2 = FakeNet()
    d2 = polboards.Discord("fmo", hook, os.path.join(tmp, "w.json"), opener=net2)
    net2.script = [(200, {"id": "w1"})]
    polboards.watch(boardfmo, args, d2, period=0.01, rounds=2)
    check("the watcher posts once and then holds",
          [c[0] for c in net2.calls] == ["POST"], [c[0] for c in net2.calls])

    print("polboards")
    check("the fmo board is registered, and in the static import block",
          polboards.BOARDS.get("fmo") == "boardfmo"
          and "boardfmo" in {a.name for n in ast.walk(ast.parse(open(polboards.__file__).read()))
                             if isinstance(n, ast.Import) for a in n.names})
    check("--fmo-port 0 (the default) starts nothing",
          polboards.build_parser().parse_args([]).fmo_port == 0)

    print("the service")
    x = socket.socket()
    x.bind(("127.0.0.1", 0))
    port = x.getsockname()[1]
    x.close()
    args = polboards.build_parser().parse_args(["--fmo-port", str(port)])
    srv = polboards.serve(boardfmo, args, port)
    base = "http://127.0.0.1:%d" % port

    def get(pth):
        try:
            with urllib.request.urlopen(base + pth, timeout=10) as r:
                return r.status, r.headers.get("Content-Type"), r.read()
        except urllib.error.HTTPError as err:
            return err.code, err.headers.get("Content-Type"), err.read()
    try:
        fresh(boardfmo)
        st, ct, body = get("/")
        check("/ is the page", st == 200 and ct.startswith("text/html") and b"City Control" in body)
        st, ct, body = get("/state.json")
        j = json.loads(body)
        check("/state.json is the snapshot", st == 200 and j["board"] == "fmo" and len(j["cities"]) == 19)
        st, ct, body = get("/render.png")
        check("/render.png is the image (or a clear 503 without art)",
              (st == 200 and ct == "image/png") or (st == 503 and png is None), (st, ct))
        st, ct, body = get("/art/fmo-title.woff2")
        check("the title face is served as font/woff2", st == 200 and ct == "font/woff2" and body[:4] == b"wOF2")
        for bad in ("/art/../boardfmo.py", "/art/%2e%2e/fmowar.py", "/art/fmo-text.ttf",
                    "/art/OFL-Oxanium.txt", "/art/nope.png"):
            check("%s is refused" % bad, get(bad)[0] == 404)
        check("/healthz", get("/healthz")[0] == 200)
    finally:
        srv.shutdown()
    check("after everything, the game data is still byte-identical",
          {k: v for k, v in tree_hash(data).items()
           if k not in ("fmo_sector_wins.json", boardfmo.LIVE_MARKER)}
          == {k: v for k, v in before.items()})
    print("[fmo_board_test] OK -- %d checks" % len(CHECKS))


if __name__ == "__main__":
    main()
