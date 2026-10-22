#!/usr/bin/env python3
"""fmo_admin_pages_test.py -- the Coliseum and war pages on the FMO tool port
(fmoserver/coliseumtool.py, fmoserver/wartool.py), over HTTP, as the admin
panel reaches them.

    python tools/fmo_admin_pages_test.py

Starts the real tool port (devtool.devtool_start, FMO_DEVTOOL_PORT on
loopback with a token) on a throwaway database (OpenLobby's tools/pgtest.py;
SKIP without Docker or POL_TEST_DATABASE_URL, POL_TEST_REQUIRE_DB=1 makes
that a failure). Pins: the token gate on every new path; each page renders
and the old ones (/, /gates) still do; coliseum.json returns the stored
document the Coliseum wrote and the live registrations and matches, and
looking writes nothing and does not create the process's Coliseum; calendar.json
lists the hosted arenas, the Clash of Iron seasons, the Saturday Heavy Mobile
Weapon Orders and the war's phases on the stored war's clock; war.json
is the City Control board's own reading plus the judged phases, the due
list and every sector; war/tick does exactly what War.tick does on a copy
of the same state, is logged with the panel's actor, and refuses once
nothing is due.
"""
import copy
import datetime
import json
import os
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SERVICES = os.path.normpath(os.path.join(HERE, os.pardir, "services"))
sys.path.insert(0, SERVICES)
os.environ.setdefault("POL_LOG_DIR", tempfile.mkdtemp(prefix="fmo-admin-logs-"))
os.environ.setdefault("FMO_RELEASE_DEFAULTS", "0")
# the NPC editor on the same port opens its layout file; never the repo's
os.environ["FMO_NPC_LAYOUT"] = os.path.join(tempfile.mkdtemp(prefix="fmo-admin-"), "layout.json")

CHECKS = []
TOKEN = "lex-arden-token"


def check(label, cond, detail=""):
    CHECKS.append(label)
    print("  %-72s %s%s" % (label, "PASS" if cond else "FAIL",
                            ("  " + str(detail)) if detail and not cond else ""), flush=True)
    if not cond:
        raise AssertionError(label)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def main():
    import fmodb
    with fmodb.test_database() as url:
        if url is None:
            print("[fmo_admin_pages_test] SKIP -- no test database")
            return
        _main()


def _main():
    port = free_port()
    os.environ.update(FMO_DEVTOOL_PORT=str(port), FMO_DEVTOOL_BIND="127.0.0.1",
                      FMO_DEVTOOL_TOKEN=TOKEN)
    os.environ.pop("POL_VALKEY_URL", None)
    import fmo  # noqa: F401 -- the facade loads the package in its order
    import fmoarena
    import fmowar
    import boardfmo
    import fedevtool
    from fmoserver import coliseum, devtool, sectorwins, warstate
    base = "http://127.0.0.1:%d" % port

    def call(path, body=None, token=TOKEN, actor=None, query_token=False):
        hdr = {}
        if token and not query_token:
            hdr["X-Devtool-Token"] = token
        if actor:
            hdr["X-Devtool-Actor"] = actor
        if token and query_token:
            path += ("&" if "?" in path else "?") + "t=" + token
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            hdr["Content-Type"] = "application/json"
        req = urllib.request.Request(base + path, data=data, headers=hdr)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, r.headers.get("Content-Type") or "", r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type") or "", e.read()

    def js(path, **kw):
        st, _ct, b = call(path, **kw)
        return st, json.loads(b)

    # ---- the stored Coliseum, as the game writes it --------------------
    now = time.time()
    day = coliseum.game_day(now)
    doc = {"hosted": {"1003": {"id": 1003, "kind": "hosted", "host": "member:11",
                               "name": "Marigold Cup", "promoter": "Lex.Arden",
                               "format": fmoarena.FORMAT_TOURNAMENT, "headcount": 3,
                               "req_bgs": 8, "bg_cost": 5, "total_cost": 15, "fee": 9000,
                               "tile": 30021, "start": int(now) + 600,
                               "end": int(now) + 7800, "created": int(now) - 60}},
           "next_id": 1004, "day": day, "hosted_today": 2,
           "records": {"1": [["Mira.Tobin", 4], ["Lex.Arden", 2]],
                       "1001": [["Tobin.Marigold", 3]]},
           "coi": {}}
    check("the Coliseum's own writer stores the seeded document", coliseum.write_state(doc))

    srv = devtool.devtool_start()
    check("devtool_start brings the tool port up with the new routes",
          srv is not None and {("GET", "/coliseum"), ("GET", "/coliseum.json"), ("GET", "/war"),
                               ("GET", "/war.json"), ("POST", "/war/tick"),
                               ("GET", "/calendar.json"), ("GET", "/gates")}
          <= set(srv.devtool_routes))
    try:
        print("the token gate")
        for p in ("/coliseum", "/coliseum.json", "/war", "/war.json?sectors=1",
                  "/calendar.json?from=0&to=86400"):
            check("%s with no token is 403" % p, call(p, token=None)[0] == 403)
            check("%s with a wrong token is 403" % p, call(p, token="mira")[0] == 403)
        check("POST /war/tick with no token is 403, and runs nothing",
              call("/war/tick", body={}, token=None)[0] == 403
              and warstate._WAR_STATE is None)
        check("the token as ?t= (how the page carries it outside the panel) is accepted",
              call("/coliseum.json", query_token=True)[0] == 200)

        print("the pages")
        for p, title in (("/coliseum", "<title>Coliseum</title>"), ("/war", "<title>War</title>")):
            st, ct, b = call(p)
            html = b.decode("utf-8")
            check("%s is an HTML page with its own title" % p,
                  st == 200 and ct.startswith("text/html") and title in html)
            check("%s: plain inline <script> only (the panel adds the nonce), no external one" % p,
                  "<script>" in html and "<script src" not in html and "src=\"http" not in html)
            check("%s: fetches relative to itself, so it works under /games/fmo/" % p,
                  ("fetch(qs('%s.json" % p[1:]) in html and "fetch('/" not in html)
            check("%s: no em dashes" % p, chr(0x2014) not in html)
        check("the NPC editor and the story gates are still served",
              call("/")[0] == 200 and call("/gates")[0] == 200
              and call("/gates.json")[0] == 200)

        print("coliseum.json, nobody at a desk yet")
        st, c = js("/coliseum.json")
        by = {a["id"]: a for a in c["arenas"]}
        check("200, the stored document is read, nothing live yet",
              st == 200 and c["ok"] and c["present"] and not c["live"])
        check("the official arenas are fmoarena's, under its schedule",
              [a["id"] for a in c["arenas"] if a["kind"] == "official"]
              == [a["id"] for a in fmoarena.OFFICIAL]
              and by[1]["rules"] == fmoarena.rule_text(fmoarena.scheduled(fmoarena.OFFICIAL[0], c["now"])))
        h = by.get(1003) or {}
        check("the hosted arena as stored: name, promoter, host, format, fee and prize",
              h.get("name") == "Marigold Cup" and h.get("promoter") == "Lex.Arden"
              and h.get("host") == "member:11" and h.get("format") == "tournament"
              and h.get("req_bgs") == 8 and h.get("fee") == 9000
              and h.get("prize") == coliseum.prize_per_win(doc["hosted"]["1003"])
              and not h.get("ended") and not h.get("closed"), h)
        check("the day's hosting count: 2 used of FMO_COLISEUM_HOSTING",
              c["hosting"]["today"] == 2
              and c["hosting"]["open"] == max(0, coliseum.HOST_CAP - 2), c["hosting"])
        recs = {r["arena"]: r for r in c["records"]}
        check("every arena's streak records, an ended arena's too",
              [(x["name"], x["streak"]) for x in recs[1]["rows"]] == [("Mira.Tobin", 4), ("Lex.Arden", 2)]
              and recs[1001]["rows"] == [{"name": "Tobin.Marigold", "streak": 3}]
              and recs[1001]["name"] == "")
        check("looking does not create the process's Coliseum, nor write the document",
              coliseum._COLISEUM is None and fmoarena.read_state() == doc)

        print("calendar.json, for the admin panel's Calendar")
        lo, hi = int(now) - 86400, int(now) + 90 * 86400
        st, cal = js("/calendar.json?from=%d&to=%d" % (lo, hi))
        ev = {e["id"]: e for e in cal.get("events") or []}
        h = ev.get("arena-1003-%d" % (int(now) + 600)) or {}
        check("the hosted tournament, from its first battle to its close",
              st == 200 and h.get("title") == "Marigold Cup" and h.get("kind") == "tournament"
              and (h.get("start"), h.get("end")) == (int(now) + 600, int(now) + 7800)
              and "Lex.Arden" in h.get("about", ""), h)
        want = fmowar.calendar(lo, hi, fmowar.PHASE1_START)
        check("the war's phases and ceasefires on its clock, every one touching the range",
              want and [ev.get(e["id"]) for e in want] == want
              and all(e["end"] > lo and e["start"] < hi for e in cal["events"]), want)
        coi = [e for e in fmoarena.calendar(lo, hi) if e["id"].startswith("coi-")]
        check("each Clash of Iron season in range: its nights and final as the sessions",
              (coi or not fmoarena.COI_SEASONS) and [ev.get(e["id"]) for e in coi] == coi
              and all(e["times"] and e["start"] == e["times"][0][0] and e["end"] == e["times"][-1][1]
                      for e in coi), coi)
        heavy = [e for e in cal["events"] if e["kind"] == "arena rule"]
        check("Heavy Mobile Weapon Orders every Saturday, 16:00 to 24:00 JST",
              len(heavy) >= 12 and all(e["end"] - e["start"] == 8 * 3600
                                       and time.gmtime(e["start"] + 9 * 3600)[3:7] == (16, 0, 0, 5)
                                       for e in heavy), heavy[:2])
        check("a range of one quiet hour has only what touches it",
              all(e["end"] > lo and e["start"] < lo + 3600
                  for e in js("/calendar.json?from=%d&to=%d" % (lo, lo + 3600))[1]["events"]))
        for q in ("", "?from=10", "?from=x&to=20", "?from=20&to=10",
                  "?from=0&to=%d" % (500 * 86400)):
            check("calendar.json%s is refused (400)" % q, call("/calendar.json" + q)[0] == 400)
        check("the calendar created neither the Coliseum nor the War, and wrote nothing",
              coliseum._COLISEUM is None and warstate._WAR_STATE is None
              and fmoarena.read_state() == doc)

        print("coliseum.json, live registrations and matches")
        col = coliseum.coliseum()
        a1 = col.find(1, now)
        e1 = col.register(a1, "member:1", ["member:1"], "Mira.Tobin", None, now - 120)
        e2 = col.register(a1, "member:2", ["member:2"], "Lex.Arden", None, now - 100)
        col.register(col.find(1003, now), "member:3", ["member:3", "member:4", "member:5"],
                          "Arden.Tobin", 77, now - 50)
        # a match under way and a decided one, shaped as start_match / finish
        # leave them (start_match needs the battle maps on disk)
        e1["match"] = e2["match"] = 7
        col.matches[7] = {"id": 7, "arena": 1, "name": a1["name"], "mapno": 161, "format": 1,
                          "teams": [e1, e2], "sides": {"member:1": 0, "member:2": 1},
                          "pushed": now - 40, "go": {"member:1": now - 25}, "verdict": None,
                          "spectators": {"member:9"}, "watch_body": b""}
        col.matches[6] = {"id": 6, "arena": 1, "name": a1["name"], "mapno": 161, "format": 1,
                          "teams": [{"name": "Marigold.Lex", "leader": "member:6", "members": ["member:6"]},
                                    {"name": "Tobin.Mira", "leader": "member:8", "members": ["member:8"]}],
                          "sides": {"member:6": 0, "member:8": 1}, "pushed": now - 900,
                          "go": {"member:6": now - 880, "member:8": now - 880},
                          "verdict": (1, "team 0 has no pilot left standing (0 vs 1)", now - 300),
                          "spectators": set(), "watch_body": b""}
        win = {"name": "Tobin.Mira", "arena": 1, "streak": 3, "members": ["member:8"]}
        col.returning["member:8"] = (win, now + 120)
        col.coi["S01"] = {"nights": {"2026-11-12": {
            "member:1": {"name": "Mira.Tobin", "wins": 3, "reached": 50.0},
            "member:2": {"name": "Lex.Arden", "wins": 3, "reached": 60.0}}}, "final": {}}
        st, c = js("/coliseum.json")
        by = {a["id"]: a for a in c["arenas"]}
        check("now the live Coliseum is read", st == 200 and c["live"])
        ents = by[1]["entries"]
        check("the official 1 vs 1's two entries, in registration order, with their match",
              [(e["name"], e["leader"], e["match"], [m["account"] for m in e["members"]]) for e in ents]
              == [("Mira.Tobin", "member:1", 7, ["member:1"]), ("Lex.Arden", "member:2", 7, ["member:2"])], ents)
        check("the hosted arena's three-pilot entry, its leader and members",
              [(e["name"], e["leader"], [m["account"] for m in e["members"]]) for e in by[1003]["entries"]]
              == [("Arden.Tobin", "member:3", ["member:3", "member:4", "member:5"])])
        check("nobody is online in this process", not any(m["online"] for e in ents for m in e["members"]))
        ms = {m["id"]: m for m in c["matches"]}
        check("the running match: teams, one of two sortied, one spectator, no verdict",
              [t["name"] for t in ms[7]["teams"]] == ["Mira.Tobin", "Lex.Arden"]
              and ms[7]["sortied"] == 1 and ms[7]["pilots"] == 2 and ms[7]["spectators"] == 1
              and ms[7]["verdict"] is None and ms[7]["mapno"] == 161, ms[7])
        check("the decided one: the winner by name and the referee's reason",
              ms[6]["verdict"]["winner"] == 1 and ms[6]["verdict"]["winner_name"] == "Tobin.Mira"
              and ms[6]["verdict"]["why"].startswith("team 0 has no pilot"), ms[6])
        check("newest match first", [m["id"] for m in c["matches"]] == [7, 6])
        check("the winner deciding on the next battle, with its deadline",
              c["returning"] == [{"account": "member:8", "name": "Tobin.Mira", "arena": 1,
                                  "streak": 3, "deadline": float(now + 120)}], c["returning"])
        s01 = next((s for s in c["coi"] if s["name"] == "S01"), None)
        if s01 is None:
            print("  (FMO_COI_SEASONS has no S01 -- the Clash of Iron check is skipped)")
        else:
            n12 = next(n for n in s01["nights"] if n["date"] == "2026-11-12")
            check("Clash of Iron: the night's standings break a tie by who got there first",
                  [(r["name"], r["wins"]) for r in n12["standings"]] == [("Mira.Tobin", 3), ("Lex.Arden", 3)]
                  and s01["qualified"] == [{"account": "member:1", "name": "Mira.Tobin",
                                            "night": "2026-11-12"}], s01)
        check("reading the live Coliseum wrote nothing and ticked nothing",
              fmoarena.read_state() == doc and col.day == day and col.hosted_today == 2
              and 1003 in col.hosted)

        print("war.json")
        st, w0 = js("/war.json")
        check("200 with no war state on file: every city Deadlock, nothing due",
              st == 200 and not w0["war"]["present"] and len(w0["cities"]) == 19
              and all(x["nation"] == 0 for x in w0["cities"]))
        check("...and looking does not create the process's War", warstate._WAR_STATE is None)
        live = warstate.war_state()
        # a war that started five months ago, so phases are due: the game's own
        # restart path (FMO_WAR_RESTART), which sets the start and does not tick
        start = (datetime.datetime.fromtimestamp(now, datetime.timezone.utc)
                 - datetime.timedelta(days=150)).strftime("%Y-%m-%d")
        live.restart(start)
        fake = {100: {60126: 0}, 200: {69118: 0}, 505: {85102: 0, 87101: 0},
                509: {94099: 0, 94101: 0, 103100: 0}, 513: {112101: 0}}
        live.seed_from_sectors(fake, force=True)
        for _ in range(3):
            live.settle(85102, fmowar.OCU, won=True, now=now)    # Maltaf -> O.C.U.
            live.settle(94101, fmowar.OCU, won=True, now=now)    # Oak Hills -> O.C.U.
            live.settle(112101, fmowar.USN, won=True, now=now)   # Peseta -> U.S.N.
        sectorwins.sector_win_record(505, 85102, fmowar.OCU, now=int(now) - 30)
        sectorwins.sector_win_record(513, 112101, fmowar.USN, now=int(now) - 10)
        stored = fmowar.read_state()
        y, mo, dd = (int(x) for x in start.split("-"))
        p1 = fmowar._ts(y, mo, dd)
        st, cal = js("/calendar.json?from=%d&to=%d" % (p1 - 86400, p1 + 30 * 86400))
        ph = [e for e in cal["events"] if e["kind"] == "war phase"]
        check("calendar.json follows the restarted war's own clock: phase 1 from %s" % start,
              st == 200 and ph and ph[0]["id"] == "phase-1" and ph[0]["start"] == p1
              and ph[0]["end"] == fmowar.phase_at(p1, start)[2], ph[:1])
        board = boardfmo.snapshot(now=now)
        st, w = js("/war.json")
        check("the phase is the board's own (boardfmo.phase)",
              st == 200 and w["phase"]["n"] == board["phase"]["n"] >= 3
              and w["phase"]["judge"] == board["phase"]["judge"], w["phase"])
        check("the score and the nineteen cities are the board's",
              w["score"] == board["score"] and w["cities"] == board["cities"]
              and (w["score"]["ocu"], w["score"]["usn"]) == (7, 4), w["score"])
        mal = next(x for x in w["cities"] if x["name"] == "Maltaf")
        check("Maltaf is O.C.U.'s at one rate step", mal["nation"] == 1 and mal["control"] == fmowar.RATE_STEP)
        check("the recent wins are the ledger's, newest first",
              [(b["area"], b["city"], b["nation"]) for b in w["recent"][:2]]
              == [("FZ-14", "Peseta", 2), ("FZ-06", "Maltaf", 1)], w["recent"])
        check("the restart is on file: this war's start, one archived war, its log line",
              w["war"]["phase1"] == start and w["war"]["restarted_to"] == start
              and len(w["wars"]) == 1 and any("war restarted" in ln for ln in w["log"]))
        twin = fmowar.War(autosave=False, load=False)
        twin.data = copy.deepcopy(stored[0])
        twin.frontline = set(live.frontline)
        twin_judged = twin.tick(now=time.time())
        check("the due list is what War.tick would run on the same state",
              w["due"] == {"judge": [pn for pn, _r in twin_judged],
                           "start": [pn for pn, _r in twin.resets_now]}
              and w["due"]["judge"] and w["due"]["start"], (w["due"], twin_judged))
        check("no judged phase yet, nothing reset", w["judged"] == [] and w["resets"] == [])
        st, ws = js("/war.json?sectors=1")
        rows = {r["tile"]: r for r in ws.get("sector_rows") or []}
        check("?sectors=1 lists every sector on file, named and placed",
              set(rows) == {int(t) for t in stored[0]["sectors"]}
              and rows[85102]["name"] == "Maltaf" and rows[85102]["area"] == "FZ-06"
              and rows[85102]["sector"] == 12 and rows[85102]["nation"] == 1
              and rows[85102]["changed"] and rows[94099]["name"] == "要塞"
              and rows[60126]["area"] == "O.C.U. Control Zone 01", rows.get(85102))
        check("without it, no sector list", "sector_rows" not in w)
        check("the knobs the rules run on",
              w["knobs"]["FMO_WAR_RATE_STEP"] == fmowar.RATE_STEP
              and w["knobs"]["FMO_WAR_COUNTER_CAP"] == fmowar.COUNTER_CAP)
        check("reading the war wrote nothing", fmowar.read_state() == stored)

        print("war/tick")
        st, r = js("/war/tick", body={}, actor="Arden")
        check("the phase clock ran: 200 ok, and says what it judged and started",
              st == 200 and r["ok"] and "judged" in r["msg"] and "started" in r["msg"], r)
        after = fmowar.read_state()[0]
        same = all({k: v for k, v in after["phases"][str(pn)].items() if k != "judged_at"}
                   == {k: v for k, v in rec.items() if k != "judged_at"}
                   for pn, rec in twin_judged)
        check("the judged phases are War.tick's on the copy (winner, points, penalty, reward)",
              sorted(after["phases"]) == sorted(str(pn) for pn, _r in twin_judged) and same,
              (after["phases"], twin.data["phases"]))
        check("the frontline reset is War.tick's too",
              {k: {x: y for x, y in v.items() if x != "at"} for k, v in after["resets"].items()}
              == {k: {x: y for x, y in v.items() if x != "at"} for k, v in twin.data["resets"].items()}
              and after["sectors"] == twin.data["sectors"])
        check("the process's War holds what was stored", live.data == after)
        check("the server log names the panel's actor",
              any("war tool: Arden ran the phase clock" in ln for ln in fedevtool.log_tail(200)))
        st, w2 = js("/war.json")
        check("war.json now shows the judged phases and nothing due",
              [j["n"] for j in w2["judged"]] == [pn for pn, _r in twin_judged]
              and w2["due"] == {"judge": [], "start": []})
        st, r2 = js("/war/tick", body={})
        check("a second run is refused: nothing is due, and nothing is written",
              st == 200 and not r2["ok"] and r2["msg"].startswith("Nothing is due")
              and fmowar.read_state()[0] == after, r2)
    finally:
        srv.shutdown()
    print("[fmo_admin_pages_test] OK -- %d checks" % len(CHECKS))


if __name__ == "__main__":
    main()
