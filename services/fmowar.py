#!/usr/bin/env python3
"""fmowar.py -- FRONT MISSION ONLINE's WAR STATE: per-sector control, the phase
clock, and the binding of both onto the 216-byte sector record.

    python fmowar.py --selftest

STATE OF PROOF (2026-09-12), stated plainly:

  * VERIFIED: THE DOOR is read off the client and seen on prod's wire: the war map
    and City Control queue a kind-7 job on the second (community) server,
    op 0x10, body {u32, u32 count, u32 ids[]}, id = 903,000,000 + ARE tile
    (fmomsn.py, `SectorQuery`). 111 such requests sit in prod's log, all
    unanswered until today.
  * VERIFIED: THE RECORD is 216 bytes with the id at +0xD0 (both callbacks copy
    0x36 dwords; the war map matches +0xD0 % 1e6 against the tile).
  * WARNING: EVERY OTHER FIELD OFFSET IS UNBOUND. The overlays draw control sign,
    control rate, B.G.Cost, the two supply rates, terrain category and NPC
    rank from this record, but which byte is which has not been read. So
    this module keeps the STATE (SE's rules) and writes it into the record
    only through a BINDING the admin supplies after the mark launch
    (`FMO_WAR=mark` -> every dword is its own offset -> read the overlays
    -> `FMO_WAR_MAP=control=0x05:b,rate=0x08:I,...`). Until then the record
    carries the id and nothing else, which the client reads as "no data".
  * The RULES are SE's own, from the archived site (audited 2026-09-12,
    the war-phase and control-rate pages): a 制圧カウンター of wins before the
    control rate ticks, PC wins moving it more than NPC wins, control
    flipping when the rate is driven to zero, ~2-month phases judged at
    12:00 on the 1st with a ceasefire until the 5th. The NUMBERS (counter
    cap, tick size) are knobs with labelled defaults, not SE's -- SE never
    published them.

Deliberately import-light (json/os/struct/time/datetime only) so fmo.py can
use it inside the container, like fmoworld.py and fmomsn.py.
"""
import argparse
import datetime
import json
import os
import struct
import sys
import time

OCU, USN = 1, 2
NATIONS = (OCU, USN)

#: Knobs (read once; fmo.py may override the module attributes for a test).
#: FMO_WAR_STATE -- the JSON file the state lives in. Default: /data (the
#: container's volume, where fmo.db lives) else next to this module.
STATE_PATH = os.environ.get("FMO_WAR_STATE", "").strip() or os.path.join(
    "/data" if os.path.isdir("/data") else os.path.dirname(os.path.abspath(__file__)),
    "fmowar.json")
#: 制圧カウンター: wins a nation needs in a sector before the control rate
#: moves (SE: "lowered the cap, raised the per-battle delta", 050628/050815;
#: the values are ours). PC-vs-PC wins count double an NPC win (SE:
#: "NPC-battle wins move the counter less", 050815:36).
COUNTER_CAP = int(os.environ.get("FMO_WAR_COUNTER_CAP", "3") or 3)
#: How far the control rate moves per full counter, in percent.
RATE_STEP = int(os.environ.get("FMO_WAR_RATE_STEP", "20") or 20)
#: A phase is ~2 months (SE: 「約2ヵ月ごとに新たなフェイズが開始」). Phase 1 starts
#: on this date (ISO, UTC); judgement is 12:00 on the 1st two months later,
#: the next phase starts 00:00 on the 5th of that month (SE's 2006 calendar).
PHASE1_START = os.environ.get("FMO_WAR_PHASE1", "2026-09-05").strip() or "2026-09-05"


#: KEY: THE ECONOMIC CITIES -- SE's phase page (guide/phase.html, audit §A2) lists
#: nineteen with 2-4 control points; the score at judgement is the sum held.
#: Each maps to ONE sector of the frontline zones by its 『』 place name in
#: the ARE table (the decoded ARE sector table, 2026-09-12): tile -> (name,
#: points, selector:row). SE's own update notes agree where they name one
#: ("06/12 Maltaf", "14/60 Peseta").
CITIES = {
    85102: ("Maltaf", 4, "505:12"), 94101: ("Oak Hills", 3, "509:4"),
    104104: ("Vienne", 4, "513:7"), 87101: ("Rousseau", 4, "505:25"),
    94104: ("Irvine", 3, "509:7"), 105101: ("Frontera", 4, "513:11"),
    87104: ("Louisbern", 4, "505:28"), 98101: ("Freedom NW", 2, "509:32"),
    109101: ("Quinston", 4, "513:39"), 89102: ("Liguria", 4, "505:40"),
    98102: ("Freedom SW", 2, "509:33"), 110099: ("Paso", 4, "513:44"),
    91101: ("Kukurika", 4, "505:53"), 99101: ("Freedom NE", 2, "509:39"),
    111104: ("San Nicolas", 4, "513:56"), 93098: ("Ambra", 4, "505:64"),
    103098: ("Grainville", 3, "509:64"), 112101: ("Peseta", 4, "513:60"),
    103102: ("White River", 3, "509:68"),
}
#: KEY: THE 0x019A CITY TABLE (lobby+0x6C36) is how the client learns WHICH
#: cities exist: 8-byte rows {u16 zone selector, u32 kind-7 id, u16 0} --
#: City Control's ctor (0x610E8934) loads ARE resource 0x140EF + selector
#: (82159 + 509 = 82668 = AI/F26/D68, FZ-10) for the city's name and hands
#: the u32 to the kind-7 query. So a city IS a sector: selector + tile.
CITY_ID_BASE = 903_000_000


def city_rows():
    """[(zone selector, tile)] for the 0x019A push, in the phase page's order."""
    return [(int(where.split(":")[0]), tile)
            for tile, (_name, _pts, where) in CITIES.items()]


#: The Deadlock penalty (phase page): the loser's fortress in Freedom City
#: (FZ area 10 = selector 509) opens the next phase unheld -- O.C.U. loss ->
#: sector 02, U.S.N. loss -> sector 66. Both rows are 『要塞』 in the ARE
#: table, and the war map asks for exactly these tiles as its zone extras
#: (0x6118C6BF..: 903094099 / 903103100).
FORTRESS = {OCU: 94099, USN: 103100}
#: FMO_WAR_SEED -- how a sector with no history starts, by its zone KIND
#: (selector // 100: 1 O.C.U. control, 2 O.C.U. occupied, 3 U.S.N. control,
#: 4 U.S.N. occupied, 5 frontline): (nation, control %). SE's opening map is
#: not published; a control zone at 100 % of its nation, an occupied zone at
#: 60 % (contested, PvP allowed there per SE), the frontline Deadlock (its
#: map resets every phase). '0' = seed nothing. Labelled defaults, not SE's.
SEED_BY_KIND = {1: (OCU, 100), 2: (OCU, 60), 3: (USN, 100), 4: (USN, 60), 5: (0, 0)}
SEED = (os.environ.get("FMO_WAR_SEED", "").strip() or "1") != "0"


def _now():
    return time.time()


def _ts(y, m, d, hh=0):
    return int(datetime.datetime(y, m, d, hh, tzinfo=datetime.timezone.utc).timestamp())


def _add_months(y, m, n):
    m0 = m - 1 + n
    return y + m0 // 12, m0 % 12 + 1


def phase_at(now=None, first=None):
    """(number, start, judge, next_start, in_ceasefire) for a moment.

    Phase N starts 00:00 on the 5th of month M, is judged 12:00 on the 1st
    of month M+2, and phase N+1 starts 00:00 on the 5th of month M+2. The
    gap is the ceasefire (sorties count for nothing). Before phase 1 there
    is no war: number 0, ceasefire True."""
    now = _now() if now is None else float(now)
    y, m, d = (int(x) for x in (first or PHASE1_START).split("-")[:3])
    start = _ts(y, m, d)
    if now < start:
        return 0, None, None, start, True
    n = 1
    while True:
        jy, jm = _add_months(y, m, 2)
        judge = _ts(jy, jm, 1, 12)
        nxt = _ts(jy, jm, 5)
        if now < nxt:
            return n, start, judge, nxt, now >= judge
        y, m, start, n = jy, jm, nxt, n + 1


def parse_binding(spec):
    """`name=offset:fmt,...` -> {name: (offset, fmt)}; fmt in b B h H i I f.
    Names: nation (signed: +1 O.C.U., -1 U.S.N., 0 Deadlock), control (0..100),
    supply_ocu, supply_usn, bg_max, bg_min, npc, terrain, counter."""
    out = {}
    for tok in (spec or "").replace(" ", "").split(","):
        if not tok:
            continue
        name, _, rest = tok.partition("=")
        off, _, fmt = rest.partition(":")
        fmt = fmt or "I"
        if fmt not in "bBhHiIf" or not name or not off:
            raise ValueError("FMO_WAR_MAP entry %r: want name=offset[:fmt] with fmt in b B h H i I f" % tok)
        o = int(off, 0)
        if not 0 <= o < 0xD8:
            raise ValueError("FMO_WAR_MAP %r: offset %#x is outside the 216-byte record" % (tok, o))
        out[name] = (o, fmt)
    return out


class War:
    """The state: {"sectors": {tile: {...}}, "phases": {n: {...}}}."""

    def __init__(self, path=None, autosave=True):
        self.path = path or STATE_PATH
        self.autosave = autosave
        self.data = {"sectors": {}, "phases": {}, "log": []}
        self.load()

    # ---- persistence ---------------------------------------------------
    def load(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                d = json.load(fh)
            if isinstance(d, dict):
                self.data.update(d)
        except (OSError, ValueError):
            pass
        return self

    def save(self):
        if not self.autosave:
            return False
        tmp = self.path + ".tmp"
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, sort_keys=True, indent=1)
            os.replace(tmp, self.path)
            return True
        except OSError:
            return False

    # ---- sectors ---------------------------------------------------------
    def sector(self, tile):
        s = self.data["sectors"].get(str(int(tile)))
        if s is None:
            s = {"nation": 0, "control": 0, "counter": {"1": 0, "2": 0},
                 "wins": {"1": 0, "2": 0}, "supply": {"1": 0, "2": 0},
                 "bg_max": 0, "bg_min": 0, "npc": 0, "terrain": 0,
                 "deadlock": False, "updated": 0}
            self.data["sectors"][str(int(tile))] = s
        return s

    def settle(self, tile, nation, won=True, pvp=False, now=None):
        """A battle in `tile` ended for `nation` (1/2); `won` says whether
        that side won. Returns (sector, what changed) -- a string for the log.

        SE's shape: wins fill a per-sector counter; when it reaches the cap
        the control rate moves by a step toward the winner; a rate driven to
        0 flips the sector to the winner at one step. A loss changes
        nothing here (the winner's own settle moves it). During a ceasefire
        nothing moves ("停戦期間の戦闘結果は…一切影響しません")."""
        s = self.sector(tile)
        n = int(nation)
        if n not in NATIONS or not won:
            return s, "no change (%s)" % ("a loss" if not won else "no nation")
        _, _, _, _, ceasefire = phase_at(now)
        s["wins"][str(n)] = s["wins"].get(str(n), 0) + 1
        if ceasefire:
            return s, "ceasefire: win recorded, control untouched"
        weight = 2 if pvp else 1
        c = s["counter"].get(str(n), 0) + weight
        if c < COUNTER_CAP:
            s["counter"][str(n)] = c
            s["updated"] = int(now or _now())
            self.save()
            return s, "counter %d/%d for nation %d" % (c, COUNTER_CAP, n)
        s["counter"][str(n)] = 0
        before = (s["nation"], s["control"])
        if s["nation"] == n:
            s["control"] = min(100, s["control"] + RATE_STEP)
        elif s["nation"] in NATIONS:
            s["control"] -= RATE_STEP
            if s["control"] <= 0:
                s["nation"], s["control"] = n, RATE_STEP
        else:                                     # nobody held it: Deadlock -> the winner
            s["nation"], s["control"] = n, RATE_STEP
        s["deadlock"] = False
        s["updated"] = int(now or _now())
        self.save()
        return s, "control %s -> (%d, %d%%)" % (before, s["nation"], s["control"])

    def sign(self, tile):
        """+1 O.C.U., -1 U.S.N., 0 Deadlock -- what 10:33..35 draw from."""
        s = self.sector(tile)
        return 0 if s.get("deadlock") else {OCU: 1, USN: -1}.get(s["nation"], 0)

    # ---- the opening map ---------------------------------------------------
    def seed_from_sectors(self, sectors_by_selector, force=False):
        """Give every sector the war has no history for its zone kind's
        opening state (SEED_BY_KIND). Kinds 1..4 first, the frontline last,
        first assignment wins for a tile shared across selectors. Returns
        how many were seeded. Idempotent: a tile already on file is left."""
        if not SEED and not force:
            return 0
        n = 0
        order = sorted(sectors_by_selector.items(),
                       key=lambda kv: (kv[0] // 100 == 5, kv[0]))
        for selector, rows in order:
            kind = int(selector) // 100
            if kind not in SEED_BY_KIND:
                continue
            nation, control = SEED_BY_KIND[kind]
            for tile in rows:
                key = str(int(tile))
                if key in self.data["sectors"]:
                    continue
                s = self.sector(tile)
                s["nation"], s["control"] = nation, control
                s["deadlock"] = nation == 0
                s["seeded"] = "kind %d" % kind
                n += 1
        if n:
            self.save()
        return n

    # ---- the phase --------------------------------------------------------
    def score(self):
        """{nation: points} -- the economic-city ranks each side holds."""
        pts = {OCU: 0, USN: 0}
        for tile, (_name, p, _where) in CITIES.items():
            s = self.data["sectors"].get(str(tile))
            if s and s["nation"] in pts and not s.get("deadlock"):
                pts[s["nation"]] += p
        return pts

    def tick(self, now=None):
        """Judge every phase whose judgement time has passed and is not yet
        on file. SE (phase page): the side with more economic-city points
        wins; a tie rewards both and penalises nobody; the loser's Freedom
        City fortress opens the next phase in Deadlock. Returns the phases
        judged now, oldest first."""
        now = _now() if now is None else float(now)
        judged = []
        n, start, judge, nxt, cease = phase_at(now)
        # phases before the current one, and the current one once judged
        for pn in range(1, n + (1 if cease else 0)):
            key = str(pn)
            if key in self.data["phases"]:
                continue
            pts = self.score()
            if pts[OCU] > pts[USN]:
                winner, loser = OCU, USN
            elif pts[USN] > pts[OCU]:
                winner, loser = USN, OCU
            else:
                winner, loser = 0, 0
            rec = {"ocu": pts[OCU], "usn": pts[USN], "winner": winner,
                   "judged_at": int(now), "penalty": None}
            if loser in FORTRESS:
                f = self.sector(FORTRESS[loser])
                f["nation"], f["control"], f["deadlock"] = 0, 0, True
                f["updated"] = int(now)
                rec["penalty"] = {"nation": loser, "tile": FORTRESS[loser]}
            self.data["phases"][key] = rec
            judged.append((pn, rec))
        if judged:
            self.save()
        return judged

    # ---- the record ------------------------------------------------------
    def record_fields(self, tile, binding):
        """{offset: bytes} for one sector under a binding (parse_binding)."""
        s = self.sector(tile)
        vals = {"nation": self.sign(tile), "control": int(s["control"]),
                "supply_ocu": int(s["supply"].get("1", 0)),
                "supply_usn": int(s["supply"].get("2", 0)),
                "bg_max": int(s["bg_max"]), "bg_min": int(s["bg_min"]),
                "npc": int(s["npc"]), "terrain": int(s["terrain"]),
                "counter": int(max(s["counter"].values() or [0]))}
        out = {}
        for name, (off, fmt) in (binding or {}).items():
            if name not in vals:
                continue
            v = vals[name]
            out[off] = struct.pack("<" + fmt, float(v) if fmt == "f" else int(v))
        return out

    def summary(self, now=None):
        held = {OCU: 0, USN: 0}
        for s in self.data["sectors"].values():
            if s["nation"] in held and not s.get("deadlock"):
                held[s["nation"]] += 1
        n, start, judge, nxt, cease = phase_at(now)
        pts = self.score()
        last = self.data["phases"].get(str(n - 1)) if n > 1 else None
        return ("phase %d%s, %d sector(s) on file, O.C.U. holds %d, U.S.N. %d; "
                "economic cities O.C.U. %d pts vs U.S.N. %d pts%s"
                % (n, " (CEASEFIRE)" if cease else "", len(self.data["sectors"]),
                   held[OCU], held[USN], pts[OCU], pts[USN],
                   (", phase %d went to %s" % (n - 1, {OCU: "O.C.U.", USN: "U.S.N."}
                                               .get(last["winner"], "a tie")))
                   if last else ""))


def selftest():
    ok = True

    def check(label, cond):
        nonlocal ok
        print(("  OK   " if cond else "  FAIL ") + label)
        ok = ok and bool(cond)

    # phase clock: SE's 2006 shape on our own start date
    t0 = _ts(2026, 9, 5)
    check("before phase 1 there is no war (phase 0, ceasefire)",
          phase_at(t0 - 1, "2026-09-05")[0] == 0 and phase_at(t0 - 1, "2026-09-05")[4])
    n, st, jd, nx, ce = phase_at(t0, "2026-09-05")
    check("phase 1 starts 2026-09-05, judged 2026-11-01 12:00, phase 2 from 2026-11-05",
          (n, st, jd, nx, ce) == (1, t0, _ts(2026, 11, 1, 12), _ts(2026, 11, 5), False))
    check("between judgement and the next start is the ceasefire",
          phase_at(_ts(2026, 11, 2), "2026-09-05")[4] is True
          and phase_at(_ts(2026, 11, 2), "2026-09-05")[0] == 1)
    check("2026-11-05 opens phase 2; 2027-01-05 phase 3 (month rollover)",
          phase_at(_ts(2026, 11, 5), "2026-09-05")[0] == 2
          and phase_at(_ts(2027, 1, 5), "2026-09-05")[0] == 3
          and phase_at(_ts(2027, 1, 5), "2026-09-05")[2] == _ts(2027, 3, 1, 12))

    # control model, in memory
    w = War(path=os.devnull, autosave=False)
    w.data = {"sectors": {}, "phases": {}, "log": []}
    mid = _ts(2026, 10, 1)                  # inside phase 1
    s, what = w.settle(69118, OCU, won=True, now=mid)
    check("a first win only fills the counter (1/3), sector still Deadlock",
          s["counter"]["1"] == 1 and s["nation"] == 0 and w.sign(69118) == 0)
    w.settle(69118, OCU, won=True, pvp=True, now=mid)   # PvP counts double -> cap
    s = w.sector(69118)
    check("a full counter flips a Deadlock sector to the winner at one step",
          s["nation"] == OCU and s["control"] == RATE_STEP and s["counter"]["1"] == 0
          and w.sign(69118) == 1)
    for _ in range(3):
        w.settle(69118, USN, won=True, now=mid)
    s = w.sector(69118)
    check("the enemy's full counter drives the rate down; at 0 the sector flips",
          s["nation"] == USN and s["control"] == RATE_STEP and w.sign(69118) == -1)
    _, what = w.settle(69118, OCU, won=False, now=mid)
    check("a loss changes nothing", "no change" in what and w.sector(69118)["nation"] == USN)
    _, what = w.settle(69118, OCU, won=True, now=_ts(2026, 11, 2))
    check("a ceasefire win is recorded but moves no counter",
          "ceasefire" in what and w.sector(69118)["counter"]["1"] == 0
          and w.sector(69118)["wins"]["1"] == 3)

    # binding -> record bytes
    b = parse_binding("nation=0x05:b,control=0x08:I,npc=0x68:B")
    f = w.record_fields(69118, b)
    check("binding writes the sign as a signed byte, the rate as u32, npc as u8",
          f[0x05] == b"\xff" and f[0x08] == struct.pack("<I", RATE_STEP) and f[0x68] == b"\x00")
    bad = False
    try:
        parse_binding("control=0x100:I")
    except ValueError:
        bad = True
    check("a binding outside the 216-byte record is refused", bad)
    check("summary reads", "phase" in w.summary())

    # seeding by zone kind, the city score, and a judgement with its penalty
    w4 = War(path=os.devnull, autosave=False)
    w4.data = {"sectors": {}, "phases": {}, "log": []}
    fake = {100: {60126: 0, 61125: 0}, 300: {64122: 0}, 200: {69118: 0},
            400: {69118: 0}, 505: {85102: 0, 87101: 0}, 509: {94099: 0, 94101: 0},
            513: {112101: 0}, 97: {10030: 0}}
    n = w4.seed_from_sectors(fake, force=True)
    check("seeding: kind 1 -> O.C.U. 100, kind 3 -> U.S.N. 100, kind 2 before 4 on a "
          "shared tile, frontline Deadlock, tutorial skipped",
          n == 9 and w4.sector(60126)["nation"] == OCU and w4.sector(60126)["control"] == 100
          and w4.sector(64122)["nation"] == USN
          and w4.sector(69118)["nation"] == OCU and w4.sector(69118)["control"] == 60
          and w4.sector(85102)["deadlock"] and "10030" not in w4.data["sectors"])
    check("seeding is idempotent", w4.seed_from_sectors(fake, force=True) == 0)
    check("score is 0:0 while the cities are Deadlock", w4.score() == {OCU: 0, USN: 0})
    for _ in range(3):
        w4.settle(85102, OCU, won=True, now=mid)      # Maltaf (4) -> O.C.U.
        w4.settle(112101, USN, won=True, now=mid)     # Peseta (4) -> U.S.N.
        w4.settle(94101, OCU, won=True, now=mid)      # Oak Hills (3) -> O.C.U.
    check("score counts held cities' points", w4.score() == {OCU: 7, USN: 4})
    check("nothing is judged inside phase 1", w4.tick(now=mid) == [])
    j = w4.tick(now=_ts(2026, 11, 2))
    check("at judgement O.C.U. wins 7:4, the U.S.N. fortress (509 sector 66) goes Deadlock",
          len(j) == 1 and j[0][0] == 1 and j[0][1]["winner"] == OCU
          and j[0][1]["penalty"] == {"nation": USN, "tile": FORTRESS[USN]}
          and w4.sector(FORTRESS[USN])["deadlock"] and w4.sector(FORTRESS[USN])["nation"] == 0)
    check("a judgement is recorded once", w4.tick(now=_ts(2026, 11, 3)) == []
          and "1" in w4.data["phases"])
    check("summary carries the score and the last result",
          "7 pts" in w4.summary(now=_ts(2026, 11, 3)) and "went to O.C.U." in w4.summary(now=_ts(2026, 11, 6)))
    check("the nineteen cities and both fortresses are distinct tiles",
          len(CITIES) == 19 and sum(p for _n, p, _w in CITIES.values()) == 66
          and FORTRESS[OCU] not in CITIES and FORTRESS[USN] not in CITIES)

    # persistence round trip
    import tempfile
    d = tempfile.mkdtemp()
    p = os.path.join(d, "w.json")
    w2 = War(path=p)
    w2.settle(70117, USN, won=True, pvp=True, now=mid)
    w2.settle(70117, USN, won=True, now=mid)
    w3 = War(path=p)
    check("state survives a reload from disk",
          w3.sector(70117)["nation"] == USN and w3.sector(70117)["control"] == RATE_STEP)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--show", action="store_true", help="print the state file's summary")
    a = ap.parse_args()
    if a.show:
        print(War().summary())
        sys.exit(0)
    sys.exit(selftest())
