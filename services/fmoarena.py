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
    it follows it, else `a` itself."""
    if not a.get("schedule"):
        return a
    v = dict(a)
    v.update(official_rules(now, a.get("headcount") or 1))
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
