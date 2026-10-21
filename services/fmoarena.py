"""fmoarena.py -- the Coliseum's OFFICIAL arenas and their schedule, shared by the
FMO server (fmoserver/coliseum.py) and the City Control board's arenas post
(boardfmo.py), the way fmowar.py is shared. Standard library only, so the board
can import it without loading the server.

Also reads the stored Coliseum document (fmo_coliseum) for the board; the server
writes it.
"""
import json
import os
import time

#: +0x39 of the arena record: 1 normal, 2 tournament (87:10 / 87:11)
FORMAT_NORMAL, FORMAT_TOURNAMENT = 1, 2

#: FMO_COLISEUM_OFFICIAL -- the official arenas, always open (SE's 2006-08-15
#: update: "there are always 1-, 3- and 5-person arenas; entry is free and no
#: prize is paid"). `headcount:bgcost:tile[:name]`, comma separated; ids
#: 1..n in order. A bgcost of `auto` follows the SCHEDULE below; a number
#: pins the arena to that cost with no rules.
OFFICIAL_SPEC = (os.environ.get("FMO_COLISEUM_OFFICIAL", "").strip()
                 or "1:auto:30001:Official 1 vs 1,3:auto:30021:Official 3 vs 3,"
                    "5:auto:30101:Official 5 vs 5")
#: THE SCHEDULE. SE's own table was an image the archive never kept; the
#: numbers are the XFM fan site's (xfm2015.web.fc2.com/fmo/fmossinfo00.html,
#: 闘技場 section, the late-era game), in JST:
#:     B.G. cost by hour, every day: 0-12 -> 5, 12-16 -> 6, 16-24 -> 4
#:     Mon/Wed/Fri  Search & Destroy   MG, SG, turbo required
#:     Tue/Thu/Sat  Sudden Death       SRF, BZ, shield, stealth, jet, turbo allowed
#:     Sun          Sudden Death       no melee, no weapon arms, jet required
#:     Sat 16-24    the 5-person arena is Heavy Mobile Weapon Orders
#: "Allowed" on Tue/Thu/Sat says those six are banned on the other days, and
#: the turbo / jet requirements override that ban; that reading is ours.
SCHEDULE_UTC_OFFSET = 9 * 3600
SCHEDULE_COST = ((0, 12, 5), (12, 16, 6), (16, 24, 4))
#: 95:0..12 and 95:13..24 (fmo-systext.tsv), the record's +0x3C / +0x49 order.
WEAPON_SLOTS = ("mg", "sg", "srf", "arf", "bz", "ca", "ms", "gr", "rk", "kn", "rd",
                "pb", "shield")
BP_SLOTS = ("radio_a", "radio_b", "repair", "light_repair", "sensor_a", "sensor_b",
            "ecm", "emp", "stealth", "jet", "item", "turbo")
#: the same slots as the game names them (95:0..24)
SLOT_NAMES = {"mg": "Machine Gun", "sg": "Shotgun", "srf": "Sniper Rifle",
              "arf": "Assault Rifle", "bz": "Bazooka", "ca": "Cannon", "ms": "Missile",
              "gr": "Grenade", "rk": "Rocket", "kn": "Knuckle", "rd": "Rod",
              "pb": "Pile Bunker", "shield": "Shield", "radio_a": "Radio A",
              "radio_b": "Radio B", "repair": "Repair", "light_repair": "Light Repair",
              "sensor_a": "Sensor A", "sensor_b": "Sensor B", "ecm": "ECM", "emp": "EMP",
              "stealth": "Stealth", "jet": "Jet", "item": "Item", "turbo": "Turbo"}
RULE_ALLOWED, RULE_BANNED, RULE_REQUIRED = 0, 1, 2
SCHEDULE_BASE_BANS = ("srf", "bz", "shield", "stealth", "jet", "turbo")
MAIN_SEARCH_DESTROY, MAIN_SCORE, MAIN_HEAVY = 0, 1, 2
SUB_NONE, SUB_SUDDEN, SUB_SUDDEN2 = 0, 1, 2
#: 89:5 / 89:0 / 89:1 and 89:2 / 89:3
MAIN_NAMES = {MAIN_SEARCH_DESTROY: "Search & Destroy", MAIN_SCORE: "Score Attack",
              MAIN_HEAVY: "Heavy Mobile Weapon Orders"}
SUB_NAMES = {SUB_SUDDEN: "Sudden Death", SUB_SUDDEN2: "Sudden Death 2"}


def official_rules(now, headcount):
    """The fields a scheduled official arena of `headcount` has at unix `now`:
    bg_cost, total_cost, weapons, bps, main_rule, sub_rule, arms."""
    t = time.gmtime(float(now) + SCHEDULE_UTC_OFFSET)
    day, hour = t.tm_wday, t.tm_hour            # tm_wday: Monday = 0
    cost = next(c for lo, hi, c in SCHEDULE_COST if lo <= hour < hi)
    rule = {s: RULE_ALLOWED for s in WEAPON_SLOTS + BP_SLOTS}
    main, sub, arms = MAIN_SEARCH_DESTROY, SUB_NONE, 1
    if day in (1, 3, 5):
        sub = SUB_SUDDEN
    else:
        rule.update({s: RULE_BANNED for s in SCHEDULE_BASE_BANS})
        if day == 6:
            sub, arms = SUB_SUDDEN, 0
            rule.update({"kn": RULE_BANNED, "rd": RULE_BANNED, "pb": RULE_BANNED,
                         "jet": RULE_REQUIRED})
        else:
            rule.update({"mg": RULE_REQUIRED, "sg": RULE_REQUIRED,
                         "turbo": RULE_REQUIRED})
    if day == 5 and hour >= 16 and int(headcount) == 5:
        main, sub = MAIN_HEAVY, SUB_NONE
    return {"bg_cost": cost, "total_cost": int(headcount) * cost,
            "weapons": [rule[s] for s in WEAPON_SLOTS],
            "bps": [rule[s] for s in BP_SLOTS],
            "main_rule": main, "sub_rule": sub, "arms": arms}


def next_change(now):
    """The unix time the official arenas' cost or rules next change: the next
    hour boundary in SCHEDULE_COST, or midnight JST (a new weekday)."""
    t = float(now) + SCHEDULE_UTC_OFFSET
    day0 = t - t % 86400
    for lo, _hi, _c in SCHEDULE_COST[1:] + ((24, 24, 0),):
        at = day0 + lo * 3600
        if at > t:
            # Saturday 16:00 is also the Heavy Mobile Weapon switch; it is a
            # cost boundary already
            return int(at - SCHEDULE_UTC_OFFSET)
    return int(day0 + 86400 - SCHEDULE_UTC_OFFSET)


def scheduled(a, now):
    """Arena `a` as it stands at `now`: a copy with the schedule applied when
    it follows it, else `a` itself. During a Clash of Iron preliminary night
    the official 1 vs 1 arena runs the season's regulation instead."""
    if not a.get("schedule"):
        return a
    v = dict(a)
    v.update(official_rules(now, a.get("headcount") or 1))
    night = coi_night(now)
    if night is not None and int(a.get("headcount") or 1) == 1:
        v.update(regulation_rules(night[0].get("regulation")))
        v["coi"] = night[0]["name"]
        v["name"] = ("Clash of Iron " + night[0]["name"])[:31]
    return v


# --------------------------------------------------------------------------- #
# CLASH OF IRON (SE's official 1 vs 1 tournament, 2006-09 .. 2008-05)
# --------------------------------------------------------------------------- #
#: SE (guide/clashofiron.html.txt, topics20060709:27-69, polnews news8685):
#: seasons of preliminary nights then an 8-pilot final. A preliminary is a
#: night in the 1 vs 1 arena under the season's regulation; entry is just
#: fighting there (no sign-up, no fee), wins need not be consecutive, and
#: the night's most wins qualifies, a tie going to whoever reached that count
#: first (news8685); an already-qualified pilot may fight but is not counted.
#: Season 01 ran Thu/Fri/Sat 21:00-23:00 JST (the guide's "Fri/Sat 21-24" is
#: not what ran). The final is single elimination in the arena's tournament
#: format, qualifiers only (SE mailed them the password), a no-show forfeits
#: and a bye is not a win; the prize is H$400,000 per final win (champion
#: 1,200,000), plus equipment SE's staff handed over in game.
#: Ours: the calendar (FMO_COI_SEASONS below; SE's dates are 2006's), the
#: reserves (the night's runner-up is not called up), and the equipment
#: prizes (not given: Solidwall / Hi Skua are not in our part tables by id).
#: FMO_COI_SEASONS: '|'-separated seasons, each
#:   name;prelims=YYYY-MM-DD,...;final=YYYY-MM-DD;reg=<regulation>
#: '0' = no Clash of Iron. Nights and the final start 21:00 JST.
COI_HOUR_JST = 21
COI_NIGHT_HOURS = 2
COI_FINAL_HOURS = 3
COI_PRIZE_PER_WIN = 400000
COI_FINAL_SIZE = 8
COI_FINAL_ID = 9001                      # the final's arena id (ours)
COI_TILE = 30001                         # the official 1 vs 1's map (ours)
#: SE's six regulations (guide/clashofiron.html.txt:21-32). S01's table
#: (topics20060709:36-69): 1 pilot, B.G.Cost 4, Search & Destroy, MG and SG
#: required, every other weapon banned, turbo required.
_W, _B = WEAPON_SLOTS, BP_SLOTS
REGULATIONS = {
    "twoguns": {"title": "Two-Guns Shootout", "main_rule": MAIN_SEARCH_DESTROY,
                "sub_rule": SUB_NONE, "require": ("mg", "sg", "turbo"),
                "only_weapons": ("mg", "sg")},
    "bazooka": {"title": "Bazooka Brawl", "main_rule": MAIN_SCORE, "sub_rule": SUB_NONE,
                "require": ("bz",), "only_weapons": ("bz",)},
    "jagerhatz": {"title": "Jagerhatz", "main_rule": MAIN_SEARCH_DESTROY,
                  "sub_rule": SUB_SUDDEN, "require": ("arf", "shield"),
                  "only_weapons": ("arf", "shield")},
    "lark": {"title": "Lark Style Melee", "main_rule": MAIN_SEARCH_DESTROY,
             "sub_rule": SUB_NONE, "require": (), "only_weapons": ("kn", "rd", "pb"),
             "only_bps": ("jet", "turbo")},
    "fistfight": {"title": "Iron Fistfight", "main_rule": MAIN_SEARCH_DESTROY,
                  "sub_rule": SUB_SUDDEN2, "require": ("kn", "jet"),
                  "only_weapons": ("kn",)},
    "gladiator": {"title": "Gladiator Battle", "main_rule": MAIN_SEARCH_DESTROY,
                  "sub_rule": SUB_SUDDEN, "require": ("shield",), "only_weapons": None},
}
#: The default calendar: SE's six regulations in SE's season order (S01
#: Two-Guns, S02 Lark Style Melee, then the rest), one season every two
#: months from 2026-11; the dates are OURS (Thu/Fri/Sat nights of two weeks,
#: the final the next Saturday).
COI_DEFAULT = ("S01;prelims=2026-11-12,2026-11-13,2026-11-14,2026-11-19,2026-11-20,"
               "2026-11-21;final=2026-11-28;reg=twoguns|"
               "S02;prelims=2027-01-14,2027-01-15,2027-01-16,2027-01-21,2027-01-22,"
               "2027-01-23;final=2027-01-30;reg=lark")


def regulation_rules(key):
    """The arena fields of regulation `key` (REGULATIONS), B.G.Cost 4."""
    r = REGULATIONS.get(key) or REGULATIONS["twoguns"]
    rule = {s: RULE_ALLOWED for s in _W + _B}
    if r.get("only_weapons"):
        rule.update({s: RULE_BANNED for s in _W if s not in r["only_weapons"]})
    if r.get("only_bps"):
        rule.update({s: RULE_BANNED for s in _B if s not in r["only_bps"]})
    rule.update({s: RULE_REQUIRED for s in r.get("require") or ()})
    return {"bg_cost": 4, "total_cost": 4, "main_rule": r["main_rule"],
            "sub_rule": r["sub_rule"], "arms": 1, "mods": 0,
            "weapons": [rule[s] for s in _W], "bps": [rule[s] for s in _B]}


def _jst(date_s, hour):
    import calendar
    y, m, d = (int(x) for x in date_s.split("-"))
    return calendar.timegm((y, m, d, hour, 0, 0)) - SCHEDULE_UTC_OFFSET


def parse_coi(spec):
    """FMO_COI_SEASONS -> [season dict]. Raises ValueError on a bad entry."""
    spec = (spec or "").strip()
    if spec == "0":
        return []
    out = []
    for part in (x.strip() for x in (spec or COI_DEFAULT).split("|")):
        if not part:
            continue
        f = part.split(";")
        s = {"name": f[0].strip(), "prelims": [], "final": None, "regulation": "twoguns"}
        for kv in f[1:]:
            k, _, v = kv.partition("=")
            k, v = k.strip(), v.strip()
            if k == "prelims":
                s["prelims"] = [d.strip() for d in v.split(",") if d.strip()]
            elif k == "final":
                s["final"] = v
            elif k == "reg":
                if v not in REGULATIONS:
                    raise ValueError(f"FMO_COI_SEASONS {part!r}: regulation {v!r} is not "
                                     f"one of {sorted(REGULATIONS)}")
                s["regulation"] = v
            else:
                raise ValueError(f"FMO_COI_SEASONS {part!r}: unknown field {k!r}")
        try:
            s["nights"] = [(d, _jst(d, COI_HOUR_JST),
                            _jst(d, COI_HOUR_JST) + COI_NIGHT_HOURS * 3600)
                           for d in s["prelims"]]
            s["final_at"] = _jst(s["final"], COI_HOUR_JST) if s["final"] else None
        except ValueError:
            raise ValueError(f"FMO_COI_SEASONS {part!r}: dates are YYYY-MM-DD")
        out.append(s)
    return out


try:
    COI_SEASONS = parse_coi(os.environ.get("FMO_COI_SEASONS", ""))
    COI_ERR = ""
except ValueError as _e:
    COI_SEASONS, COI_ERR = [], str(_e)


def coi_night(now, seasons=None):
    """(season, night date) when a preliminary night is on at `now`, else None."""
    for s in (COI_SEASONS if seasons is None else seasons):
        for d, lo, hi in s.get("nights") or ():
            if lo <= float(now) < hi:
                return s, d
    return None


def coi_final_window(now, seasons=None):
    """The season whose final is listed at `now` (from its last night's end
    until the final's end), else None."""
    for s in (COI_SEASONS if seasons is None else seasons):
        if not s.get("final_at"):
            continue
        last = max((hi for _d, _lo, hi in s.get("nights") or ()), default=s["final_at"])
        if last <= float(now) < s["final_at"] + COI_FINAL_HOURS * 3600:
            return s
    return None


def coi_standings(night_rows):
    """[(account, name, wins, reached)] best first: most wins, then whoever
    reached that count first (news8685). `night_rows` = {account: {name,
    wins, reached}}. Pure."""
    rows = [(a, r.get("name", ""), int(r.get("wins") or 0), float(r.get("reached") or 0))
            for a, r in (night_rows or {}).items()]
    rows.sort(key=lambda r: (-r[2], r[3]))
    return rows


def coi_qualifiers(season_doc):
    """[(account, name, night)] the pilots a season's nights have qualified,
    one per night, in night order. `season_doc` = {"nights": {date: rows}}."""
    out, seen = [], set()
    for d in sorted((season_doc or {}).get("nights") or {}):
        for a, name, wins, _t in coi_standings(season_doc["nights"][d]):
            if wins > 0 and a not in seen:
                out.append((a, name, d))
                seen.add(a)
                break
    return out[:COI_FINAL_SIZE]


def coi_final_arena(season):
    """The season's final as an arena row: a 1-pilot tournament of 8."""
    v = {"id": COI_FINAL_ID, "kind": "coi", "format": FORMAT_TOURNAMENT,
         "name": ("Clash of Iron %s Final" % season["name"])[:31],
         "promoter": "Coliseum", "headcount": 1, "req_bgs": COI_FINAL_SIZE,
         "tile": COI_TILE, "fee": 0, "dup_bp": 1, "coi": season["name"],
         "start": int(season["final_at"]),
         "end": int(season["final_at"]) + COI_FINAL_HOURS * 3600}
    v.update(regulation_rules(season.get("regulation")))
    return v


def parse_official(spec):
    """FMO_COLISEUM_OFFICIAL -> [arena dict]. Raises ValueError on a bad entry."""
    out = []
    for part in (x.strip() for x in (spec or "").split(",")):
        if not part:
            continue
        f = part.split(":")
        if len(f) < 3:
            raise ValueError(f"FMO_COLISEUM_OFFICIAL entry {part!r}: want "
                             f"headcount:bgcost:tile[:name]")
        auto = f[1].strip().lower() == "auto"
        hc, cost, tile = int(f[0]), (0 if auto else int(f[1])), int(f[2])
        if not 1 <= hc <= 5:
            raise ValueError(f"FMO_COLISEUM_OFFICIAL {part!r}: headcount 1..5")
        name = ":".join(f[3:]) or f"Official {hc} vs {hc}"
        out.append({"id": len(out) + 1, "kind": "official", "format": FORMAT_NORMAL,
                    "name": name, "promoter": "Coliseum", "headcount": hc,
                    "bg_cost": cost, "total_cost": hc * cost, "tile": tile,
                    "main_rule": 0, "sub_rule": 0, "mods": 1, "arms": 1,
                    "dup_bp": 1, "fee": 0, "start": 0, "end": 0,
                    "schedule": auto})
    return out


try:
    OFFICIAL = parse_official(OFFICIAL_SPEC)
    OFFICIAL_ERR = ""
except ValueError as _e:
    OFFICIAL, OFFICIAL_ERR = [], str(_e)


def rule_text(a):
    """One line for arena `a`'s rules, as the detail window words them:
    "Sudden Death; required: Jet; banned: Knuckle, Rod, Pile Bunker, weapon arms"."""
    parts = [MAIN_NAMES.get(int(a.get("main_rule") or 0), "Search & Destroy")]
    sub = SUB_NAMES.get(int(a.get("sub_rule") or 0))
    if sub:
        parts[0] = sub if parts[0] == "Search & Destroy" else "%s, %s" % (parts[0], sub)
    slots = list(zip(WEAPON_SLOTS, a.get("weapons") or ())) + list(zip(BP_SLOTS, a.get("bps") or ()))
    req = [SLOT_NAMES[s] for s, v in slots if v == RULE_REQUIRED]
    ban = [SLOT_NAMES[s] for s, v in slots if v == RULE_BANNED]
    if not a.get("arms", 1):
        ban.append("weapon arms")
    if req:
        parts.append("required: " + ", ".join(req))
    if ban:
        parts.append("banned: " + ", ".join(ban))
    return "; ".join(parts)


def read_state():
    """The stored Coliseum document (fmo_coliseum), or None (nothing on file,
    or no database)."""
    try:
        import fmodb
    except ImportError:
        return None
    try:
        fmodb.ready()
        row = fmodb.db.query_one("SELECT data FROM fmo_coliseum WHERE id = 1")
    except fmodb.ERRORS:
        return None
    if not row:
        return None
    try:
        d = json.loads(row["data"])
    except ValueError:
        return None
    return d if isinstance(d, dict) else None
