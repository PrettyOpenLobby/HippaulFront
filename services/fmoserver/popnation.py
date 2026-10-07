"""Nation and battle side on a POP, and which side an enemy stands on."""
import os


def character_nation(c):
    """(nation, source) for a stored character: creation +0x28, 1 = O.C.U.,
    2 = U.S.N. -- the `nation_byte` key on a record written after 2026-08-26,
    else read out of `raw`, else (None, why). WARNING: NEVER the `nation` key: that
    is creation +0x26, the GENDER (the swapped-key hazard). Same shape and reason as
    _gender_byte / _look_field; every consumer of the nation must come
    through here so the swapped key cannot be read by accident again."""
    if not c:
        return None, "no character"
    if c.get("nation_byte") is not None:
        return c["nation_byte"], "character store [nation_byte] (creation +0x28)"
    v = poplook._look_field(c, "nation_byte", 0x28)
    if v is not None:
        return v, "character store raw[+0x28] (record predates the key)"
    return None, "no nation on file (a name-step record with no 0x013E yet)"


#: VERIFIED:KEY: FMO_UDP_POP_NATION -- the self-POP nation byte (body+0x7C -> entity+0x1C3),
#: the 0xE060 cast-switch discriminant the lobby script tests before it runs its
#: own 0xE280 cast create. Live 2026-09-05 (fmoe280 shim trace) the client read
#: 0 there and skipped EVERY cast NPC (operators, Map Selector, Scramble Board,
#: Nina); the switch runs the OCU cast on 1, the USN cast on 2 (read
#: statically, 2026-09-05).
#:
#: WARNING: DEFAULT OFF. A POP-body change is world-entry-critical (the
#: look block and the stub body both black-screened the lobby), so this ships
#: off and must be confirmed in a live session before it becomes the default. Values:
#:   ""/0/off  -> OFF (body+0x7C stays 0, the current behaviour)
#:   auto      -> the host character's nation via character_nation() (swap-safe)
#:   1 / 2     -> force O.C.U. / U.S.N.
POP_NATION_KNOB = os.environ.get("FMO_UDP_POP_NATION", "").strip()
if POP_NATION_KNOB.lower() not in ("", "0", "off", "auto", "1", "2"):
    raise SystemExit(
        "FMO_UDP_POP_NATION=%r: legal values are auto, 1 (O.C.U.), "
        "2 (U.S.N.), or empty/0/off" % POP_NATION_KNOB)


def pop_nation_for(host_ip):
    """(nation, source) for the self-POP body+0x7C, or (None, why) when OFF or
    unresolved. Only 1/2 are sent; record_pop refuses anything else."""
    k = POP_NATION_KNOB.lower()
    if k in ("", "0", "off"):
        return None, "FMO_UDP_POP_NATION off"
    if zoneentry.NATION_PER_CHARACTER and k in ("1", "2"):
        # 2026-09-08: a forced value is the FALLBACK for a pilot with no
        # nation on file, not an override of one who has -- the byte is the
        # 0xE060 cast switch, and forcing it is what put the O.C.U. cast in
        # front of a U.S.N. pilot.
        for c in charstore.load_roster(identity.account_for(host_ip)):
            n, src = character_nation(c)
            if n in (1, 2):
                return n, ("FMO_NATION_PER_CHARACTER: " + src
                           + " (FMO_UDP_POP_NATION=%s is the fallback)" % k)
            break
    if k in ("1", "2"):
        return int(k), "FMO_UDP_POP_NATION=%s (forced)" % POP_NATION_KNOB
    if k != "auto":
        raise SystemExit(
            "FMO_UDP_POP_NATION=%r: legal values are auto, 1 (O.C.U.), "
            "2 (U.S.N.), or empty/0/off" % POP_NATION_KNOB)
    acct = identity.account_for(host_ip)
    for c in charstore.load_roster(acct):
        n, src = character_nation(c)
        if n in (1, 2):
            return n, "FMO_UDP_POP_NATION=auto -> " + src
        return None, "FMO_UDP_POP_NATION=auto but " + src   # first char, no nation
    return None, "FMO_UDP_POP_NATION=auto but no character on file"


#: KEY: THE BATTLE SIDE IS THE NATION (2026-09-11). body+0x27 -> unit+0x80 is
#: the friend/foe byte every targeting arm compares (fmoworld.POP_SIDE), and
#: the side NAMES we push in cmd 134 are A = "O.C.U.", B = "U.S.N." -- so an
#: O.C.U. pilot (nation 1) is side 0 and a U.S.N. pilot (nation 2) is side 1,
#: and an enemy is the other one. Until now the player's own battle unit was
#: popped with side 0 whatever their nation and the dummy's side was a knob
#: (FMO_BATTLE_DUMMY_SIDE) the operator had to set by hand to 1 -- two
#: numbers for one fact, neither read from the pilot. The lookup is the
#: character's own nation (creation +0x28), NOT the FMO_UDP_POP_NATION knob:
#: that knob gates the lobby CAST switch and is off by default, and the side
#: byte must be right even when nobody armed it. Lobby (human) pops keep
#: side 0: the human creators key the MODEL on that byte (== 1 -> 0x17), so
#: it is only ever set on a battle (wanzer) pop.
NATION_SIDE = {1: 0, 2: 1}


def battle_side_for(host_ip):
    """(side, source) for this host's pilot on a battle pop, or (None, why).
    In an arena match the side is the pilot's TEAM, whatever its nation."""
    from . import coliseum               # late: coliseum imports half the package
    _team = coliseum.side_for_key(host_ip)
    if _team is not None:
        return _team, f"arena match team {_team} (coliseum.py)"
    if not charstore.CHAR_STORE:
        return None, "no character store"
    for c in charstore.load_roster(identity.account_for(host_ip)):
        n, src = character_nation(c)
        if n in NATION_SIDE:
            return NATION_SIDE[n], f"nation {n} -> side {NATION_SIDE[n]} ({src})"
        return None, "first character has no nation: " + src
    return None, "no character on file"


def enemy_missions_running(mapno, tile, zone, viewer_side, now=None):
    """[(host, accept)] -- pilots of the OTHER side fighting on battle map
    `mapno` now (warmap_census) who hold an open battle-map mission whose
    battlefield is (zone, tile). SE's "an enemy battle-map mission is
    running": its taker created a battle map on the target sector."""
    if viewer_side not in (0, 1) or not mapno or not tile:
        return []
    _n = servicerecord._now_unix(now)
    _counts, _start, who = warmap.warmap_census(
        mapno, referee.BATTLE_STATE, lambda h: battle_side_for(h)[0], _n, missionblock.MISSION_TIME)
    out = []
    for host, side in who:
        if side == viewer_side:
            continue
        for c in charstore.load_roster(identity.account_for(host))[:1]:
            for m in missionbook.accepted_missions(c):
                if (missionbook.mission_status(m, _n) == "open"
                        and m.get("cat") in (None, 1)
                        and int(m.get("sector") or 0) == int(tile)
                        and (not m.get("zone") or zone is None
                             or int(m["zone"]) == int(zone))):
                    out.append((host, m))
    return out


def enemy_side_for(host_ip):
    """The side an ENEMY of this host's pilot stands on: the other one."""
    side, src = battle_side_for(host_ip)
    if side is None:
        return None, src
    return 1 - side, f"the other side of {src}"


# Called at run time only; imported last so that import cycles resolve.
from . import (  # noqa: E402
    charstore, identity, missionblock, missionbook, poplook, referee, servicerecord, warmap,
    zoneentry,
)
