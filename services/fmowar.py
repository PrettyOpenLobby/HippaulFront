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
    control rate ticks, PC wins moving it more than NPC wins, a loss
    moving it back, a sector driven to zero going neutral (Deadlock) before
    it changes hands, lower caps on fortresses and bases, a frontline reset
    when a new phase starts (rules added 2026-09-30), ~2-month phases judged at
    12:00 on the 1st with a ceasefire until the 5th. The NUMBERS (counter
    cap, tick size) are knobs with labelled defaults, not SE's -- SE never
    published them.

Deliberately import-light (json/os/struct/time/datetime; the database through
fmodb, imported when the state is first read or written) so the board and the
tests can use the rules without a database, like fmoworld.py and fmomsn.py.

WHERE THE STATE LIVES. One JSON document in the stack's PostgreSQL database
(the fmo_war table, see fmodb.py), read whole and written whole as the file
fmowar.json was until 2026-09. The fmo service loads the state when it
starts (fmoserver/warstate.py, load_at_start), and the first writer to find
the table empty imports that file once (FMO_WAR_STATE, else
/data/fmowar.json), logs the sector count or why it could not, and leaves the
file as it was. `python fmodb.py import war FILE` imports it by hand.
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
#: FMO_WAR_STATE -- the JSON file the state lived in before it moved into the
#: database: imported once into an empty table, never written. Default: /data
#: (the container's volume) else next to this module.
LEGACY_PATH = os.environ.get("FMO_WAR_STATE", "").strip() or os.path.join(
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


def _env_int(name, default):
    """int() of an env knob, EMPTY = UNSET. The same guard as
    fmoserver/knobs._env_int, repeated here because this module stays
    import-light (importing the fmoserver package pulls in the whole server)."""
    return int(os.environ.get(name, "").strip() or str(default), 0)


#: FMO_WAR_LOSS -- A LOSS LOWERS THE LOSER'S CONTROL RATE. SE's tutorial
#: (AI/F00/D08 78): 「制圧率はセクターごとに存在し、勝利すると制圧率が上昇、
#: 敗北することで制圧率が減少します」. A battle lost against NPCs fills the
#: ENEMY's counter by this weight, as if the NPC side had won it (NPC wins
#: weigh 1, SE 050815: 「NPC戦の制圧カウンター変動値を、PC戦と比較して更に
#: 小さく」). A PvP loss moves nothing here: the winners' own settles already
#: moved it, at double weight. 0 = the old rule, a loss changes nothing.
#: The weight is ours, to tune.
LOSS_WEIGHT = _env_int("FMO_WAR_LOSS", 1)
#: FMO_WAR_NEUTRAL -- A SECTOR CHANGES HANDS THROUGH NEUTRAL. SE's manual
#: supplement (guide/addmanual.html 93): selecting a sector on the war map
#: cycles its holder 「敵軍→中立→自軍」, and the neutral state is Deadlock
#: (guide/phase: 「Deadlock状態(いずれの陣営にも制圧されていない状態)」;
#: systext 10:33..35 O.C.U. Control / U.S.N. Control / Deadlock). So a rate
#: driven to 0 leaves the sector Deadlock (nation 0), and only the NEXT full
#: counter hands it to the winner at one step. On the kind-7 record this is
#: the `nation` sign 0 we already write for Deadlock; which raw byte the
#: client draws as "Deadlock" is not read yet (FMO_WAR_MAP is unbound), so
#: 0 is our encoding, not a proven one. 0 = the old straight flip.
NEUTRAL = _env_int("FMO_WAR_NEUTRAL", 1) != 0
#: FMO_WAR_FACILITY_CAP -- the control-rate CAP of a fortress or base, in
#: percent (every other sector caps at 100). SE, update 050815:
#: 「制圧カウンター上限値を各セクターに応じて引き下げ…更に、要塞・戦略拠点の
#: 制圧率上限を引き下げたことで、より少ない勝利数でセクターを制圧することが
#: 可能となります」. SE gave no number; 60 is ours, to tune, and it is the value
#: that reproduces SE's own worked example (topics 0906mission): a radar base
#: fully held by the enemy stops its radar after ONE stage 「制圧率を一段階
#: 変動させるだけで」 and becomes ours after 「さらに三段階」 -- 60 -> 40 (radar
#: off), 40 -> 20, 20 -> Deadlock, Deadlock -> ours at 20: 1 + 3 steps of
#: RATE_STEP 20. An ordinary city at 100 needs 6, SE's 「三段階以上」.
#: 0 = no per-sector cap (every sector at 100, the old rule).
FACILITY_CAP = _env_int("FMO_WAR_FACILITY_CAP", 60)
#: FMO_WAR_PHASE_RESET -- A NEW PHASE RESETS THE FRONTLINE. SE (guide/phase):
#: 「激戦区の戦局図は、新たなフェイズの開始と同時にリセットされます」, and the
#: 2006-06-05 notice (polnews news7740): 「停戦に伴い、激戦区セクター状況を
#: 初期化しました。なお初期化に伴い、停戦時に実行中のエリアミッションはすべて
#: 自動的に失敗となります」 with the loser's fortress starting Deadlock. At the
#: start of phase N >= 2 every frontline sector goes back to its opening
#: state, once. 0 = the old rule (the frontline carries over; only the
#: loser's fortress changes).
PHASE_RESET = _env_int("FMO_WAR_PHASE_RESET", 1) != 0
#: FMO_WAR_RESTART -- START THE WAR AGAIN on a chosen date (YYYY-MM-DD, UTC),
#: without touching the database by hand. When the stored state was not
#: already restarted to this date, the fmo service (a writer) archives the
#: judged phases under data["wars"], drops every sector so seeding rebuilds
#: the opening map, and makes this date phase 1's start. Each date runs
#: once: leaving the knob set is harmless, and a new date restarts again.
#: Empty (default) = never. The same thing by hand, with the service stopped:
#: `python fmowar.py --restart YYYY-MM-DD`.
RESTART = os.environ.get("FMO_WAR_RESTART", "").strip()

#: KEY: THE VICTORY REWARD SERIES (guide/phase: 「勝利すると、それまで購入できな
#: かった相手陣営のヴァンツァー1シリーズが、ハンガーから購入できるようになります」,
#: sold for good in later phases; a tie rewards both sides). key -> (SE's
#: name, ids). The ids are the same in the body (0x11), arms (0x21) and legs
#: (0x31) master tables, read out of the client's Data\AG\F21\D97.DAT
#: (2026-10-07). SE's "Tiran/カローク" (phase 01, U.S.N.) names a カローク
#: we cannot find in the tables by any spelling, so that series is Tiran
#: alone; the trailing-space "Arpeggio " row 561 is left out.
SERIES = {
    "arpeggio-orgel": ("Arpeggio/Orgel", (191, 192, 193, 194)),
    "tiran": ("Tiran", (176, 177, 178, 179, 180)),
    "pabotte": ("Pabotte", (186, 187, 188, 189, 190)),
    "vyzov": ("Vyzov", (286, 287, 288, 289, 290)),
    "stork-varsa": ("Stork/Varsa", (121, 122, 123, 124, 125)),
    "igel-grille": ("Igel/Grille", (181, 182, 183, 184)),
}
SERIES_KINDS = (0x11, 0x21, 0x31)          #: body, arms, legs: one series
#: SE's table as run (topics 2006-05-19 and 2006-07-10): phase -> {winner:
#: series}. Phases 01-02 gave the winner one ENEMY series; from phase 03 the
#: series was a player vote among three per side (P03 Vyzov for both, P04
#: O.C.U. Vyzov / U.S.N. Stork/Varsa). The vote is not built: a phase with no
#: row here (and none in FMO_WAR_REWARDS) keeps the 01-02 rule, taking the
#: first series of REWARD_ORDER the winner does not hold yet.
REWARD_TABLE = {1: {OCU: "arpeggio-orgel", USN: "tiran"},
                2: {OCU: "arpeggio-orgel", USN: "pabotte"}}
#: The 01-02 rule's queue per winner, SE's own later picks first, then
#: the phase-00 provisional series (topics060306). Ours, as an order.
REWARD_ORDER = {OCU: ("arpeggio-orgel", "vyzov", "igel-grille"),
                USN: ("tiran", "pabotte", "stork-varsa", "vyzov")}


def parse_rewards(spec):
    """FMO_WAR_REWARDS `phase:nation=series,...` (e.g. `3:1=vyzov,3:2=vyzov`)
    -> {phase: {nation: series}}, laid over REWARD_TABLE; '0' = no reward at
    all (None). This is the knob a player vote's result goes into."""
    spec = (spec or "").replace(" ", "")
    if spec == "0":
        return None
    out = {p: dict(row) for p, row in REWARD_TABLE.items()}
    for tok in spec.split(","):
        if not tok:
            continue
        where, _, series = tok.partition("=")
        ph, _, nat = where.partition(":")
        if not ph.isdigit() or nat not in ("1", "2") or series not in SERIES:
            raise ValueError("FMO_WAR_REWARDS entry %r: want phase:nation=series with "
                             "nation 1 or 2 and series one of %s" % (tok, sorted(SERIES)))
        out.setdefault(int(ph), {})[int(nat)] = series
    return out


REWARDS = parse_rewards(os.environ.get("FMO_WAR_REWARDS", ""))


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
#: KEY: THE FORTRESSES AND BASES of the three live frontline zones (505, 509,
#: 513), every ARE row whose 『』 name is a 要塞 or a 基地, read out of the
#: decoded ARE sector table (2026-09-30): tile -> "selector:row name". SE's 050815 note lowers the cap
#: of 「要塞・戦略拠点」; which facilities SE counted as 戦略拠点 is not
#: published, so every base is in (ours). Frontline tiles are shared with no
#: other selector, so the tile alone names the sector.
FACILITY = {
    85098: "505:8 対空ミサイル基地", 85104: "505:14 防衛基地",
    87102: "505:26 防衛基地", 88103: "505:34 戦略ミサイル基地",
    89101: "505:39 防衛基地", 90098: "505:43 仮設駐屯基地",
    90100: "505:45 レーダー基地", 91099: "505:51 防衛基地",
    91100: "505:52 対空防衛基地", 91103: "505:55 レーダー基地",
    91104: "505:56 防衛基地", 92103: "505:62 仮設駐屯基地",
    94099: "509:2 要塞", 97103: "509:27 レーダー基地",
    98104: "509:35 対空ミサイル基地", 99099: "509:37 レーダー基地",
    100102: "509:47 レーダー基地", 100104: "509:49 対空防衛基地",
    103100: "509:66 要塞", 104100: "513:3 対空ミサイル基地",
    105099: "513:9 防衛基地", 106098: "513:15 仮設駐屯基地",
    106100: "513:17 レーダー基地", 106103: "513:20 仮設駐屯基地",
    107101: "513:25 防衛基地", 107103: "513:27 レーダー基地",
    107104: "513:28 防衛基地", 110101: "513:46 防衛基地",
    110103: "513:48 戦略ミサイル基地", 112099: "513:58 対空防衛基地",
    112103: "513:62 防衛基地", 113098: "513:64 レーダー基地",
    113101: "513:67 モーガン要塞",
}


def control_cap(tile):
    """The highest control rate `tile` can reach: FACILITY_CAP for a
    fortress or base (SE 050815), else 100. Never below one RATE_STEP."""
    if FACILITY_CAP and int(tile) in FACILITY:
        return max(RATE_STEP, min(100, FACILITY_CAP))
    return 100
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


def _parse_date(s):
    """'YYYY-MM-DD' -> the same string, or ValueError."""
    datetime.date(*(int(x) for x in str(s).split("-")[:3]))
    return "-".join("%02d" % int(x) if i else "%04d" % int(x)
                    for i, x in enumerate(str(s).split("-")[:3]))


def reward_for(pn, nation, held, table=None):
    """The series key nation `nation` wins with phase `pn`, or None. The
    table's row first (FMO_WAR_REWARDS over REWARD_TABLE); when there is no
    row, or the nation already holds that series, SE's 01-02 rule: the first
    series of REWARD_ORDER it does not hold yet. `held` = series keys this
    nation has won before. Pure."""
    table = REWARDS if table is None else table
    if table is None:
        return None
    want = (table.get(int(pn)) or {}).get(int(nation))
    if want and want not in held:
        return want
    for key in REWARD_ORDER.get(int(nation), ()):
        if key not in held:
            return key
    return None


def rewards_held(phases, nation, upto=None):
    """Series keys `nation` has been awarded by the judged `phases` (only
    phases before `upto` when given)."""
    out = []
    for k, rec in sorted((phases or {}).items(), key=lambda kv: int(kv[0])):
        if upto is not None and int(k) >= int(upto):
            continue
        r = ((rec or {}).get("reward") or {}).get(str(int(nation)))
        if isinstance(r, dict) and r.get("series"):
            out.append(r["series"])
    return out


def reward_unlocked(phases, now=None):
    """{nation: {kind: set(ids)}} of every victory series ON SALE at `now`:
    awarded by a judged phase whose `reward_from` (the next phase's start,
    SE: 「フェイズ02開始以降、ハンガーのショップから」) has passed. Phase
    records without a "reward" (judged before rewards were recorded) give
    nothing here. Pure."""
    now = _now() if now is None else float(now)
    out = {}
    for rec in (phases or {}).values():
        if not isinstance(rec, dict) or now < float(rec.get("reward_from") or 0):
            continue
        for nat, r in (rec.get("reward") or {}).items():
            if not isinstance(r, dict):
                continue
            for kind in r.get("kinds") or SERIES_KINDS:
                out.setdefault(int(nat), {}).setdefault(int(kind), set()).update(
                    int(i) for i in r.get("parts") or ())
    return out


def reward_candidates(nation):
    """{kind: set(ids)} every series the reward rules could ever give
    `nation`: what its shop must NOT sell before it is won."""
    keys = set(REWARD_ORDER.get(int(nation), ()))
    for row in (REWARDS or {}).values():
        if row.get(int(nation)):
            keys.add(row[int(nation)])
    ids = set()
    for k in keys:
        ids.update(SERIES[k][1])
    return {kind: set(ids) for kind in SERIES_KINDS}


def reward_stock(stock, nation, phases, now=None):
    """The 0x016A stock {kind: frozenset(ids)} with the recorded victory
    series applied for a pilot of `nation`: every series it has ON SALE
    added, every other series the rules could give it removed (SE: the
    enemy series 「それまで購入できなかった」). `nation` None (no pilot to ask)
    adds every nation's series and removes nothing. None when empty. Pure."""
    out = {k: set(v) for k, v in (stock or {}).items()}
    on_sale = reward_unlocked(phases, now)
    if nation in NATIONS:
        mine = on_sale.get(int(nation), {})
        for kind, ids in reward_candidates(nation).items():
            if kind in out:
                out[kind] -= ids - mine.get(kind, set())
        adds = [mine]
    else:
        adds = list(on_sale.values())
    for got in adds:
        for kind, ids in got.items():
            out.setdefault(kind, set()).update(ids)
    out = {k: frozenset(v) for k, v in out.items() if v}
    return out or None


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


def _fmodb():
    """fmodb, or None when polcore (OpenLobby) is not importable."""
    try:
        import fmodb
        return fmodb
    except ImportError:
        return None


def read_state():
    """(data, updated_at) as stored, or (None, None) when nothing is on file
    or the database cannot be read. Never writes."""
    fdb = _fmodb()
    if fdb is None:
        return None, None
    try:
        fdb.ready()
        row = fdb.db.query_one("SELECT data, updated_at FROM fmo_war WHERE id = 1")
    except fdb.ERRORS:
        return None, None
    if not row:
        return None, None
    try:
        d = json.loads(row["data"])
    except ValueError:
        return None, float(row["updated_at"])
    return (d if isinstance(d, dict) else None), float(row["updated_at"])


def write_state(data, now=None):
    """Store the whole document. False when the database cannot be written."""
    fdb = _fmodb()
    if fdb is None:
        return False
    try:
        fdb.ready()
        fdb.db.upsert("fmo_war", {"id": 1,
                                  "data": json.dumps(data, sort_keys=True, indent=1),
                                  "updated_at": float(_now() if now is None else now)},
                      key="id")
        return True
    except fdb.ERRORS:
        return False


def read_legacy(path):
    """The war state an old fmowar.json holds, as a dict. Raises OSError
    when the file cannot be read and ValueError when it is not a war state
    (not JSON, or not a JSON object)."""
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    if not isinstance(d, dict):
        raise ValueError("%s holds a %s, not a war state object"
                         % (path, type(d).__name__))
    return d


def import_legacy(path=None, log=None):
    """Fill an EMPTY fmo_war from the old fmowar.json, once. Returns True when
    it imported. The file is left as it was. `log`, when given, is told what
    happened: the sector count imported, or why nothing was (no file, a table
    that already holds the state, a file or a database that failed)."""
    say = log or (lambda _msg: None)
    path = path or LEGACY_PATH
    if not path or not os.path.exists(path):
        say("no old war state file at %s; nothing to import" % path)
        return False
    fdb = _fmodb()
    if fdb is None:
        say("WARNING: %s not imported: the database module (fmodb) is not "
            "importable" % path)
        return False
    try:
        d = read_legacy(path)
    except (OSError, ValueError) as exc:
        say("WARNING: %s not imported: %s" % (path, exc))
        return False
    try:
        fdb.ready()
        with fdb.db.transaction(lock="fmo_war") as conn:
            if fdb.db.query_one("SELECT 1 AS x FROM fmo_war WHERE id = 1", conn=conn):
                say("fmo_war already holds the war state; %s is not read "
                    "(fmodb.py import war compares the two)" % path)
                return False
            fdb.db.execute("INSERT INTO fmo_war (id, data, updated_at)"
                           " VALUES (1, %s, %s)",
                           (json.dumps(d, sort_keys=True, indent=1),
                            os.path.getmtime(path)), conn=conn)
    except fdb.ERRORS + (OSError,) as exc:
        say("WARNING: %s not imported: the database failed (%s)" % (path, exc))
        return False
    say("imported %s: %d sector(s), %d judged phase(s)"
        % (path, len(d.get("sectors") or {}), len(d.get("phases") or {})))
    return True


class War:
    """The state: {"sectors": {tile: {...}}, "phases": {n: {...}}}.

    War() reads the stored state and writes every change back (autosave).
    War(autosave=False) only reads: the board uses it. load=False starts
    empty and in memory, for the tests that build a war by hand."""

    def __init__(self, autosave=True, load=True):
        self.autosave = autosave
        self.data = {"sectors": {}, "phases": {}, "log": []}
        self.present = False            # was there a stored state
        self.updated_at = None          # when it was last written (epoch s)
        self.frontline = set()          # kind-5 tiles seeding saw (not stored)
        self.resets_now = []            # [(phase, record)] the last tick reset
        if load:
            self.load()

    # ---- persistence ---------------------------------------------------
    def load(self):
        d, at = read_state()
        if at is None and self.autosave and import_legacy():
            d, at = read_state()
        if at is not None:
            self.present, self.updated_at = True, at
        if isinstance(d, dict):
            self.data.update(d)
        if self.autosave and RESTART and self.data.get("restarted_to") != RESTART:
            self.restart(RESTART)
        self.adopt_phase1()
        return self

    # ---- the phase clock this war runs on --------------------------------
    def phase1(self):
        """Phase 1's start date for THIS war: the stored one (set by the
        first writer, or by a restart), else FMO_WAR_PHASE1."""
        return str(self.data.get("phase1") or PHASE1_START)

    def adopt_phase1(self):
        """Make the stored start the process's clock, so phase_at() callers
        without a War (defection, the board) agree with the judged phases.
        A state with no start yet takes FMO_WAR_PHASE1 (saved with the next
        write). A knob that disagrees with a stored start is NOT a restart:
        the stored start wins, and FMO_WAR_RESTART is the way to move it."""
        global PHASE1_START
        self.data.setdefault("phase1", PHASE1_START)
        PHASE1_START = self.phase1()
        return PHASE1_START

    def phase_at(self, now=None):
        return phase_at(now, self.phase1())

    def restart(self, date, now=None):
        """Start the war again with phase 1 on `date` (YYYY-MM-DD, UTC). The
        judged phases, resets and start move to data["wars"] (kept, never
        read by the rules); every sector is dropped so the opening map is
        seeded again (seed_from_sectors, which warstate.war_state runs right
        after loading); the restart moment fails running area missions the
        way a phase reset does (frontline_reset_at). Returns the archive."""
        date = _parse_date(date)
        now = int(_now() if now is None else now)
        old = {"phase1": self.data.get("phase1"), "phases": self.data.get("phases") or {},
               "resets": self.data.get("resets") or {},
               "sectors": len(self.data.get("sectors") or {}), "ended_at": now}
        self.data.setdefault("wars", []).append(old)
        self.data["sectors"], self.data["phases"], self.data["resets"] = {}, {}, {}
        self.data["phase1"], self.data["restarted_to"] = date, date
        self.data["restarted_at"] = now
        self.data.setdefault("log", []).append(
            "%d: war restarted, phase 1 from %s (was %s, %d judged phase(s) archived)"
            % (now, date, old["phase1"], len(old["phases"])))
        self.adopt_phase1()
        self.save()
        return old

    def save(self):
        if not self.autosave:
            return False
        if write_state(self.data):
            self.present = True
            return True
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
        the control rate moves by a step toward the winner. A loss against
        NPCs fills the ENEMY's counter (LOSS_WEIGHT, AI/F00/D08 78). A rate
        driven to 0 leaves the sector neutral, Deadlock, and the next full
        counter hands it to the winner at one step (NEUTRAL, addmanual 93:
        「敵軍→中立→自軍」). A fortress or base caps at FACILITY_CAP (050815).
        During a ceasefire nothing moves ("停戦期間の戦闘結果は…一切影響しません")."""
        s = self.sector(tile)
        n = int(nation)
        if n not in NATIONS:
            return s, "no change (no nation)"
        _, _, _, _, ceasefire = self.phase_at(now)
        if won:
            s["wins"][str(n)] = s["wins"].get(str(n), 0) + 1
            mover, weight = n, (2 if pvp else 1)
        else:
            lost = s.setdefault("losses", {"1": 0, "2": 0})
            lost[str(n)] = lost.get(str(n), 0) + 1
            if not LOSS_WEIGHT or pvp:
                self.save()
                return s, ("no change (a loss%s)" % (
                    " in PvP: the winners' own settles move the counter" if LOSS_WEIGHT
                    else "; FMO_WAR_LOSS=0"))
            # the side that beat us: the enemy's counter fills, as an NPC win
            mover, weight = (USN if n == OCU else OCU), LOSS_WEIGHT
        if ceasefire:
            self.save()
            return s, "ceasefire: %s recorded, control untouched" % ("win" if won else "loss")
        c = s["counter"].get(str(mover), 0) + weight
        if c < COUNTER_CAP:
            s["counter"][str(mover)] = c
            s["updated"] = int(now or _now())
            self.save()
            return s, "counter %d/%d for nation %d%s" % (
                c, COUNTER_CAP, mover, "" if won else " (nation %d's loss)" % n)
        s["counter"][str(mover)] = 0
        before = (s["nation"], s["control"])
        cap = control_cap(tile)
        if s["nation"] == mover:
            s["control"] = min(cap, s["control"] + RATE_STEP)
        elif s["nation"] in NATIONS:
            s["control"] = min(cap, s["control"]) - RATE_STEP
            if s["control"] <= 0:
                if NEUTRAL:                       # enemy -> neutral (Deadlock)
                    s["nation"], s["control"] = 0, 0
                else:                             # the old straight flip
                    s["nation"], s["control"] = mover, min(cap, RATE_STEP)
        else:                                     # neutral (Deadlock) -> the winner
            s["nation"], s["control"] = mover, min(cap, RATE_STEP)
        s["deadlock"] = s["nation"] not in NATIONS
        s["updated"] = int(now or _now())
        self.save()
        return s, "control %s -> (%d, %d%%)%s%s" % (
            before, s["nation"], s["control"],
            " NEUTRAL (Deadlock)" if s["deadlock"] else "",
            "" if won else " on nation %d's loss" % n)

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
            if kind == 5:
                # the frontline, remembered for the phase reset
                self.frontline.update(int(t) for t in rows)
            if kind not in SEED_BY_KIND:
                continue
            for tile in rows:
                key = str(int(tile))
                if key in self.data["sectors"]:
                    continue
                s = self.sector(tile)
                nation, control = self.opening(tile, kind)
                s["nation"], s["control"] = nation, control
                s["deadlock"] = nation == 0
                s["seeded"] = "kind %d" % kind
                n += 1
        if n:
            self.save()
        return n

    @staticmethod
    def opening(tile, kind):
        """(nation, control) a sector opens a phase with. SEED_BY_KIND by zone
        kind, except the two Freedom City fortresses, which open held by
        their own side at their cap: SE's penalty is that the LOSER's
        fortress starts Deadlock (guide/phase), and on a tie 「両軍とも要塞を
        放棄することなく同条件で国境が再設定されます」, so unpenalised they
        start held."""
        for nat, t in FORTRESS.items():
            if int(tile) == t and int(kind) == 5:
                return nat, control_cap(t)
        return SEED_BY_KIND[int(kind)]

    def frontline_tiles(self):
        """Every frontline (kind 5) tile this war knows: the ones seeding saw,
        the ones seeded as kind 5 on file, and the cities, fortresses and
        facilities (all of them frontline sectors)."""
        out = set(self.frontline) | set(CITIES) | set(FORTRESS.values()) | set(FACILITY)
        for key, s in self.data["sectors"].items():
            if s.get("seeded") == "kind 5":
                out.add(int(key))
        # a tile seeded by another kind is not the frontline's to reset
        return {t for t in out
                if (self.data["sectors"].get(str(t)) or {}).get("seeded", "kind 5") == "kind 5"}

    def reset_frontline(self, pn, at, frontline=True):
        """SE: the frontline goes back to its opening state when phase `pn`
        starts, and the previous phase's loser's fortress starts Deadlock
        (「次のフェイズの開始時に」). `frontline` False (FMO_WAR_PHASE_RESET=0)
        applies the penalty alone. Returns the record kept under
        data["resets"][pn]."""
        prev = self.data["phases"].get(str(pn - 1)) or {}
        pen = prev.get("penalty") or {}
        n = 0
        tiles = self.frontline_tiles() if frontline else set()
        if pen:
            tiles.add(int(pen.get("tile") or 0))
        for tile in sorted(tiles):
            if str(tile) not in self.data["sectors"] and tile not in FORTRESS.values():
                continue                         # never touched: already its opening
            s = self.sector(tile)
            nation, control = self.opening(tile, 5)
            if pen and int(pen.get("tile") or 0) == tile:
                nation, control = 0, 0           # the loser's fortress: Deadlock
            elif not frontline:
                continue
            s["nation"], s["control"] = nation, control
            s["deadlock"] = nation == 0
            s["counter"] = {"1": 0, "2": 0}
            s["reset"] = int(pn)
            s["updated"] = int(at)
            n += 1
        return {"at": int(at), "sectors": n, "penalty": pen or None,
                "frontline": bool(frontline)}

    def frontline_reset_at(self):
        """When the frontline was last reset (the start of that phase, epoch
        s), or 0. SE (news7740): area missions running at the reset fail, so
        an area accept older than this is failed; the mission book reads it."""
        return max([int(r.get("at") or 0) for r in (self.data.get("resets") or {}).values()
                    if r.get("frontline", True)]
                   + [int(self.data.get("restarted_at") or 0)])

    # ---- the phase --------------------------------------------------------
    def score(self):
        """{nation: points} -- the economic-city ranks each side holds."""
        pts = {OCU: 0, USN: 0}
        for tile, (_name, p, _where) in CITIES.items():
            s = self.data["sectors"].get(str(tile))
            if s and s["nation"] in pts and not s.get("deadlock"):
                pts[s["nation"]] += p
        return pts

    def judge(self, pn, now, start_next):
        """The record for phase `pn`, judged on the map as it stands. SE
        (guide/phase): the side holding more economic-city ranks wins; a tie
        rewards both and penalises nobody; the loser's Freedom City fortress
        is named for the Deadlock penalty, applied when the next phase starts
        (reset_frontline). Each winner (both on a tie) is awarded one series
        (reward_for), on sale from the next phase's start."""
        pts = self.score()
        if pts[OCU] > pts[USN]:
            winners, loser = (OCU,), USN
        elif pts[USN] > pts[OCU]:
            winners, loser = (USN,), OCU
        else:
            winners, loser = (OCU, USN), 0
        rec = {"ocu": pts[OCU], "usn": pts[USN],
               "winner": winners[0] if len(winners) == 1 else 0,
               "judged_at": int(now), "war": self.phase1(), "penalty": None,
               "reward": {}, "reward_from": int(start_next)}
        if loser in FORTRESS:
            rec["penalty"] = {"nation": loser, "tile": FORTRESS[loser]}
        for nat in winners:
            key = reward_for(pn, nat, rewards_held(self.data["phases"], nat, upto=pn))
            if key:
                rec["reward"][str(nat)] = {"series": key, "name": SERIES[key][0],
                                           "parts": list(SERIES[key][1]),
                                           "kinds": list(SERIES_KINDS)}
        return rec

    def tick(self, now=None):
        """Judge every phase whose judgement time has passed and is not yet
        on file (once: a phase on file is never judged again), then start
        the current phase if that has not run: the frontline reset and the
        loser's fortress Deadlock. Returns the phases judged now, oldest
        first; self.resets_now holds the phase start run now."""
        now = _now() if now is None else float(now)
        judged = []
        n, start, judge, nxt, cease = self.phase_at(now)
        # phases before the current one, and the current one once judged
        for pn in range(1, n + (1 if cease else 0)):
            key = str(pn)
            if key in self.data["phases"]:
                continue
            rec = self.judge(pn, now, self._start_of(pn + 1))
            self.data["phases"][key] = rec
            judged.append((pn, rec))
        # SE: the frontline resets when a NEW phase starts (guide/phase,
        # news7740), after the judgement above has named the loser. Phase 1
        # is the war's opening, never a reset. Phases skipped while the
        # server was down collapse into one start at the current phase's
        # start, and each is recorded so none runs twice. The Deadlock
        # penalty runs here even with FMO_WAR_PHASE_RESET=0.
        self.resets_now = []
        if n >= 2:
            done = self.data.setdefault("resets", {})
            todo = [pn for pn in range(2, n + 1) if str(pn) not in done]
            if todo:
                rec = self.reset_frontline(n, start, frontline=PHASE_RESET)
                for pn in todo:
                    done[str(pn)] = rec if pn == n else {"at": int(start), "sectors": 0,
                                                         "superseded_by": n}
                self.resets_now.append((n, rec))
        if judged or self.resets_now:
            self.save()
        return judged

    def _start_of(self, pn):
        """Epoch s at which phase `pn` (>= 1) starts on this war's clock."""
        y, m, d = (int(x) for x in self.phase1().split("-")[:3])
        t = _ts(y, m, d)
        for _ in range(int(pn) - 1):
            t = phase_at(t, self.phase1())[3]
        return t

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
        n, start, judge, nxt, cease = self.phase_at(now)
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
    w = War(autosave=False, load=False)
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
    check("the enemy's full counter drives the rate down; at 0 the sector goes "
          "NEUTRAL (Deadlock), not straight across (addmanual 93)",
          s["nation"] == 0 and s["deadlock"] and w.sign(69118) == 0)
    for _ in range(3):
        w.settle(69118, USN, won=True, now=mid)
    s = w.sector(69118)
    check("the next full counter hands the neutral sector over at one step",
          s["nation"] == USN and s["control"] == RATE_STEP and w.sign(69118) == -1)
    _, what = w.settle(69118, OCU, won=False, now=mid)
    check("an NPC loss fills the ENEMY's counter (AI/F00/D08 78)",
          "loss" in what and w.sector(69118)["counter"]["2"] == 1
          and w.sector(69118)["losses"]["1"] == 1)
    _, what = w.settle(69118, OCU, won=False, pvp=True, now=mid)
    check("a PvP loss moves nothing (the winners' settles do)",
          "no change" in what and w.sector(69118)["counter"]["2"] == 1)
    w.sector(69118)["counter"]["2"] = 0
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
    w4 = War(autosave=False, load=False)
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
    check("at judgement O.C.U. wins 7:4 and the U.S.N. fortress (509 sector 66) is named "
          "for the penalty, still held through the ceasefire (SE: at the next phase start)",
          len(j) == 1 and j[0][0] == 1 and j[0][1]["winner"] == OCU
          and j[0][1]["penalty"] == {"nation": USN, "tile": FORTRESS[USN]}
          and str(FORTRESS[USN]) not in w4.data["sectors"])
    check("a judgement is recorded once", w4.tick(now=_ts(2026, 11, 3)) == []
          and "1" in w4.data["phases"])
    check("summary carries the score and the last result",
          "7 pts" in w4.summary(now=_ts(2026, 11, 3)) and "went to O.C.U." in w4.summary(now=_ts(2026, 11, 6)))
    w4.tick(now=_ts(2026, 11, 5, 1))
    check("phase 2's start puts the U.S.N. fortress in Deadlock",
          w4.sector(FORTRESS[USN])["deadlock"] and w4.sector(FORTRESS[USN])["nation"] == 0)
    check("the nineteen cities and both fortresses are distinct tiles",
          len(CITIES) == 19 and sum(p for _n, p, _w in CITIES.values()) == 66
          and FORTRESS[OCU] not in CITIES and FORTRESS[USN] not in CITIES)
    check("seeding opens each Freedom City fortress held by its own side at its cap",
          w4.sector(FORTRESS[OCU])["nation"] == OCU
          and w4.sector(FORTRESS[OCU])["control"] == control_cap(FORTRESS[OCU]))

    # per-sector caps (050815) and the 0906mission radar-base arithmetic
    w5 = War(autosave=False, load=False)
    w5.data = {"sectors": {}, "phases": {}, "log": []}
    for t, cap in ((90100, control_cap(90100)), (85102, 100)):   # radar base, city
        s = w5.sector(t)
        s["nation"], s["control"] = USN, cap
    steps = {}
    for t in (90100, 85102):
        k = 0
        while w5.sector(t)["nation"] != OCU and k < 20:
            for _ in range(COUNTER_CAP):
                w5.settle(t, OCU, won=True, now=mid)
            k += 1
        steps[t] = k
    check("a fully enemy radar base is ours in 1 + 3 steps (0906mission), a city in 6",
          (not FACILITY_CAP or steps[90100] == 4) and steps[85102] == 6)
    for _ in range(COUNTER_CAP * 5):
        w5.settle(90100, OCU, won=True, now=mid)
    check("a base never climbs past its cap", w5.sector(90100)["control"] == control_cap(90100))

    # the phase reset (guide/phase, news7740)
    w6 = War(autosave=False, load=False)
    w6.data = {"sectors": {}, "phases": {}, "log": []}
    w6.seed_from_sectors({100: {60126: 0}, 509: {94099: 0, 94101: 0, 103100: 0}}, force=True)
    for _ in range(3):
        w6.settle(94101, OCU, won=True, now=mid)          # Oak Hills -> O.C.U.
    w6.tick(now=_ts(2026, 11, 2))
    check("no reset inside the ceasefire", w6.frontline_reset_at() == 0
          and w6.sector(94101)["nation"] == OCU)
    w6.tick(now=_ts(2026, 11, 5, 1))
    check("phase 2 opens with the frontline reset, the loser's fortress Deadlock, "
          "the winner's held, the Controlled Zone untouched",
          (not PHASE_RESET) or (w6.frontline_reset_at() == _ts(2026, 11, 5)
                                and w6.sector(94101)["deadlock"]
                                and w6.sector(FORTRESS[USN])["deadlock"]
                                and w6.sector(FORTRESS[OCU])["nation"] == OCU
                                and w6.sector(60126)["nation"] == OCU
                                and w6.sector(60126)["control"] == 100))
    w6.settle(94101, OCU, won=True, now=_ts(2026, 11, 6))
    w6.tick(now=_ts(2026, 11, 7))
    check("a phase resets once", w6.sector(94101)["counter"]["1"] == 1)

    # persistence round trip, in a throwaway database
    fdb = _fmodb()
    if fdb is None:
        print("  SKIP persistence (polcore is not importable)")
    else:
        import tempfile
        with fdb.test_database() as url:
            if url is None:
                print("  SKIP persistence (no test database)")
            else:
                global LEGACY_PATH
                legacy_was, LEGACY_PATH = LEGACY_PATH, os.path.join(
                    tempfile.mkdtemp(), "fmowar.json")
                try:
                    check("nothing on file: an empty war, not present",
                          War(autosave=False).present is False
                          and read_state() == (None, None))
                    w2 = War()
                    w2.settle(70117, USN, won=True, pvp=True, now=mid)
                    w2.settle(70117, USN, won=True, now=mid)
                    w3 = War()
                    check("state survives a reload from the database",
                          w3.present and w3.sector(70117)["nation"] == USN
                          and w3.sector(70117)["control"] == RATE_STEP)
                    at = read_state()[1]
                    War(autosave=False).sector(1234)
                    check("a read-only War never writes",
                          read_state()[1] == at and "1234" not in read_state()[0]["sectors"])
                    # the old file fills an EMPTY table once, and only a writer
                    # imports it
                    fdb.db.execute("DELETE FROM fmo_war")
                    with open(LEGACY_PATH, "w", encoding="utf-8") as fh:
                        json.dump({"sectors": {"85102": {
                            "nation": OCU, "control": 40, "counter": {"1": 0, "2": 0},
                            "wins": {"1": 0, "2": 0}, "supply": {"1": 0, "2": 0},
                            "bg_max": 0, "bg_min": 0, "npc": 0, "terrain": 0,
                            "deadlock": False, "updated": 0}},
                            "phases": {}, "log": []}, fh)
                    check("a reader does not import the old file",
                          War(autosave=False).present is False)
                    w4 = War()
                    check("a writer imports it into an empty table",
                          w4.present and w4.sector(85102)["control"] == 40
                          and read_state()[0]["sectors"]["85102"]["nation"] == OCU)
                    w4.settle(85102, USN, won=True, pvp=True, now=mid)
                    check("and never again: the table wins over the file",
                          import_legacy() is False
                          and War().sector(85102)["counter"]["2"] == 2)
                finally:
                    LEGACY_PATH = legacy_was
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--show", action="store_true", help="print the stored state's summary")
    ap.add_argument("--restart", metavar="YYYY-MM-DD",
                    help="start the war again with phase 1 on this date (UTC): archive the "
                         "judged phases, reseed the opening map. STOP the fmo service first "
                         "(it holds the state in memory and would write over this); with it "
                         "running, set FMO_WAR_RESTART and recreate it instead")
    a = ap.parse_args()
    if a.restart:
        w = War(autosave=True)
        old = w.restart(a.restart)
        try:
            import fmosectors
            seeded = w.seed_from_sectors(fmosectors.SECTORS, force=True)
        except ImportError:
            seeded = 0
        w.data["restarted_to"] = _parse_date(a.restart)
        ok = w.save()
        print("%s: phase 1 from %s, %d judged phase(s) archived, %d sector(s) seeded; %s"
              % ("restarted" if ok else "NOT WRITTEN (database?)", w.phase1(),
                 len(old["phases"]), seeded, w.summary()))
        sys.exit(0 if ok else 1)
    if a.show:
        print(War().summary())
        sys.exit(0)
    sys.exit(selftest())
