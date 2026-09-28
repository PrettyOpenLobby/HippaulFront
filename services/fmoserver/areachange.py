"""Area change: which map a zone change grants."""
import os
from . import zoneentry


#: KEY: `0x0151` -- **THE CHANGE AREA REQUEST**. Measured LIVE
#: 2026-09-04, 05:48:54Z: with FMO_ZONE_CONTROL armed a player picked FZ-14
#: in Change Area and the client SENT SOMETHING -- the first byte ever to leave
#: that screen. Body is 12 B, and payload+0x00 is a u16 zone id: `01 02` =
#: 0x0201 = **513** = FZ-14: Peseta, one of the exactly three rows we served.
#:
#: WARNING: This retires "Change Area sends NOTHING" (2026-09-03) and it retires
#: the prediction that the id to watch for would be 0x0170. It is 0x0151.
#:
#: THE CONTRACT (builder `0x6117981E`ff, state at lobby+0x740E, all in
#: `kycli_lobmain.cpp`):
#:   * the screen calls `0x61179570(zone*)`, which stores the pointer at
#:     lobby+0x7412 and sets state 1. Its one caller is 0x6101014C -- inside the
#:     Change Area module, which is how we know what this message IS.
#:   * the send arm builds id 0x151 with a 0xC body whose first word is
#:     `word [[lobby+0x7412]]`, and stores the poll sequence at lobby+0x7416.
#:   * the poll arm `0x611796B0` waits on that sequence, then
#:         cmp word [esi+6], 0x153
#:     KEY: **THE REPLY IS A 0x0153** -- the same grant world entry and a Move
#:     get. On a match it writes the picked zone into **lobby+0x4F06**, which is
#:     MapKind, i.e. the field the HUD renders through the D83 zone table; then
#:     it consumes our payload exactly as world entry does: `rep movsd 0x3F`
#:     (252 B) from payload+0x28 into lobby+0x6B3A, `rep movsd 0x16` (88 B) from
#:     payload+0x124 into globals+0xD0, and 0x14 bytes from the payload head
#:     into [lobby+0x7412]+0xC.
#:   * the mismatch arm `0x611797DE` takes `word [esi+8]` -- our packet's CONN
#:     field -- as an error code, sets state 3, and special-cases 0xC91C/0xC91E.
#:     So a non-0x0153 reply is a graceful refusal, and only silence hangs.
#:
#: WARNING: WHAT WE GRANT, AND WHAT WE DO NOT. Serving the 0x0153 makes the client
#: believe it is in the picked zone: the HUD line and Change Area's own
#: "you are here" both read lobby+0x4F06. But MapNo is left at whatever the
#: session already has unless FMO_AREA_CHANGE_MAPNO says otherwise, so the
#: player stays in the room they are standing in and only the LABEL changes.
#:
#: KEY: THE REASON IS THE ID SPACE, NOT A MISSING FILE. An earlier draft of
#: this note said "we do not have a map for FZ-14", which is wrong and was
#: corrected the same day. Measured with `fmofile.py scan`:
#:     type 1 (base 53557)  281 present  -- the mission / battle maps
#:     type 2 (base 55605)   12 present  -- MapNo, exactly VALID_MAPNOS
#:     type 3 (base 93545) 1000 present  -- scripts, by MapKind
#: The FZ-14 battlefield is almost certainly among those 281. It is simply not
#: reachable from HERE: 0x61005100 registers the 0x0153's MapNo as a **type 2**
#: id, and only the twelve lobby maps exist in that space, so a warzone id in
#: this field resolves to a zero-length resource and takes the 0x611250A2
#: double-relocation crash. The type-1 maps load on the SORTIE path
#: (0x0139 -> 0x013A SelectBattleMap -> scene 4), which is a different door and
#: one 0x0139 has still never been sent through.
#:
#: So granting the label alone is the smallest step that proves the round trip,
#: and the log says plainly that is all it is.
MSG_AREA_CHANGE_REQ = 0x0151
AREA_CHANGE_LEN = 0xC                  # the `push 0xC` at 0x61179848
AREA_CHANGE_ZONE_OFF = 0x00            # u16, `mov cx, word [edx]` at 0x6117985A
#: '1' (default) = grant, replying 0x0153 with MapKind = the requested zone.
#: '0' = stay silent, which leaves the client polling forever (the shape the
#: 0x0159 hang had) -- for reproducing, not for running.
AREA_CHANGE = os.environ.get("FMO_AREA_CHANGE", "1").strip() or "1"
#: WHERE AN AREA CHANGE ACTUALLY SENDS YOU.
#:   ""              (default) keep the session's current MapNo -- the LABEL
#:                   changes and the player does not move.
#:   "<n>"           force one MapNo for every area.
#:   "zone:mapno,..."  per zone, e.g. "505:122,509:121,513:123".
#:   "auto"          a stable round-robin of ZONE_ROWS_D83 over VALID_MAPNOS,
#:                   so every selectable area lands somewhere different and a
#:                   move visibly moves you.
#: WARNING: EVERY DESTINATION MUST BE A REAL type-2 MapNo. The 0x0153's MapNo is a
#: type-2 resource id and only the twelve in VALID_MAPNOS exist; a warzone id
#: there is the 0x611250A2 crash. The warzone's own terrain is a TYPE-1 map
#: reached through the sortie (0x0139/0x013A/0x014C), not through this grant.
#: WARNING: So `auto` is a STAND-IN: it makes moving move you, to a lobby room, not
#: to FZ-14. It is a test mapping we invented, not SE's -- nothing in the
#: install links a zone to a lobby map.
AREA_CHANGE_MAPNO = os.environ.get("FMO_AREA_CHANGE_MAPNO", "").strip()
#: WARNING: MEASURED CRASH, 2026-09-04. `script_id_for` substitutes the nation
#: script (98/99) for every MapKind EXCEPT 600..607. Inside that band the client
#: loads the per-MapKind script -- 600 -> type 3 id 600 -> AJ/F41/D45.DAT,
#: 459,520 B, which exists -- and runs it against whatever MapNo we grant. The
#: player picked the Coliseum with MapNo still at 121 and the client CRASHED,
#: where 505 and 513 (both substituted to script 98) survived the same map. So
#: this band is the one place a mismatched MapNo is known to be fatal rather
#: than merely wrong. 1 (default) = refuse a 600..607 area change unless that
#: zone has an explicit destination; 0 = grant it anyway.
AREA_CHANGE_STRICT = os.environ.get("FMO_AREA_CHANGE_STRICT", "1") != "0"
#: The MapKind band script_id_for leaves alone (0x258..0x25F).
UNSUBSTITUTED_BAND = (0x258, 0x25F)

#: KEY: FMO_ZONE_MAPNO -- WHICH LOBBY MAP EACH ZONE STANDS IN (2026-09-08).
#: `sel:mapno,...` where `sel` is a zone id (407) or a zone KIND (4 = every
#: 4xx). The most specific row wins; a zone with no row keeps whatever the
#: caller had (FMO_MAPNO at world entry, the session's map on an area change).
#: Every destination must be one of the twelve type-2 maps, checked at import.
#:
#: WARNING: NOTHING IN THE CLIENT DATA BINDS A ZONE TO A MAP -- SE's server held that
#: (fmo-lobby-models-and-room-mismatch). What the data DOES say: the LEV table
#: runs ONE script (AI/F00/D87) for both factions' HQ and occupation lobbies,
#: and that script places its cast at ONE set of coordinates whichever nation
#: branch runs, so SE's two factions' lobbies shared a floor plan; 101 and 102
#: are the only maps whose floor (y 2.99) carries those coordinates; 102 is the
#: one every cutscene has been proved on. So the default is 102 for every zone
#: and the DIFFERENCE between zones is the instance (ROOM_SAME_ZONE), the cast
#: (roster_for) and the label -- not the room. `3:101,4:101` is the one-line
#: experiment that gives the U.S.N. a different hall (101, the hangar), and it
#: costs the U.S.N. story cutscenes their staging: D87's marks at x 13..17
#: are inside 101's east wall.
ZONE_MAPNO_SPEC = os.environ.get("FMO_ZONE_MAPNO", "").strip()


def parse_zone_mapno(spec):
    """`sel:mapno,...` -> {sel: mapno}; a bad row or a map that does not exist
    is a SystemExit at import (a silent knob is the worst kind)."""
    out = {}
    for e in (spec or "").replace(" ", "").split(","):
        if not e:
            continue
        sel, _, mn = e.partition(":")
        try:
            sel, mn = int(sel, 0), int(mn, 0)
        except ValueError:
            raise SystemExit(f"FMO_ZONE_MAPNO entry {e!r}: wants "
                             f"<zone-or-kind>:<mapno>")
        if mn not in zoneentry.VALID_MAPNOS:
            raise SystemExit(f"FMO_ZONE_MAPNO entry {e!r}: MapNo {mn} is not "
                             f"one of the twelve type-2 maps {zoneentry.VALID_MAPNOS} -- "
                             f"a missing map crashes the client at 0x611250A2")
        out[sel] = mn
    return out


ZONE_MAPNO = parse_zone_mapno(ZONE_MAPNO_SPEC)


def zone_mapno(mapkind, default, default_src, table=None):
    """(mapno, why) for a zone: its own row, else its kind's row, else
    `default`. Pure; `table` is for the selftest."""
    table = ZONE_MAPNO if table is None else table
    if mapkind is not None:
        if mapkind in table:
            return table[mapkind], f"FMO_ZONE_MAPNO[{mapkind}]"
        kind = mapkind // 100
        if kind in table:
            return table[kind], f"FMO_ZONE_MAPNO[kind {kind}]"
    return default, default_src


def area_change_destinations():
    """zoneId -> MapNo, or {} when the knob is unset / a bare int."""
    spec = AREA_CHANGE_MAPNO
    if not spec or ":" not in spec and spec.lower() != "auto":
        return {}
    if spec.lower() == "auto":
        # WARNING: auto must NOT pair the 600..607 band. Those MapKinds are the ones
        # script_id_for leaves unsubstituted, so the client loads per-MapKind
        # script 600.. against whatever MapNo we grant -- and NO type-2 lobby
        # map is a valid pairing (the script wants a type-1 warzone). Auto's
        # destinations are arbitrary lobby rooms, so pairing them here would
        # hand the STRICT guard below a "deliberately paired" zone and crash the
        # client (MEASURED LIVE 2026-09-04: a player picked zone 600 with
        # auto, the grant went out, the client CLOSED 2 s later). Excluding the
        # band leaves those zones unpaired, so the STRICT guard refuses them
        # gracefully. An explicit FMO_AREA_CHANGE_MAPNO=600:<n> is still an
        # operator's deliberate override.
        return {z: zoneentry.VALID_MAPNOS[i % len(zoneentry.VALID_MAPNOS)]
                for i, (z, _o, _u) in enumerate(zonecontrol.ZONE_ROWS_D83)
                if not UNSUBSTITUTED_BAND[0] <= z <= UNSUBSTITUTED_BAND[1]}
    out = {}
    for e in spec.replace(" ", "").split(","):
        if not e:
            continue
        z, _, m = e.partition(":")
        out[int(z, 0)] = int(m, 0)
    return out


def area_change_mapno(zone, current):
    """The MapNo to grant for `zone`, and the reason, as (mapno, why).

    Precedence (2026-09-08): an EXPLICIT per-zone FMO_AREA_CHANGE_MAPNO row,
    then FMO_ZONE_MAPNO (the zone's own map -- the same table world entry
    uses, so an area change lands where a fresh login to that zone would),
    then `auto`'s round-robin stand-in, then a flat value, then the session's
    current map.

    WARNING: Refuses to hand back anything outside VALID_MAPNOS: an unknown type-2
    id resolves to a zero-length resource and takes the client down.
    """
    dests = area_change_destinations()
    is_auto = AREA_CHANGE_MAPNO.lower() == "auto"
    zm, zm_why = zone_mapno(zone, None, "")
    if zone in dests and not is_auto:
        want, why = dests[zone], "FMO_AREA_CHANGE_MAPNO"
    elif zm is not None:
        want, why = zm, zm_why
    elif zone in dests:
        want, why = dests[zone], "FMO_AREA_CHANGE_MAPNO=auto (a stand-in)"
    elif AREA_CHANGE_MAPNO and ":" not in AREA_CHANGE_MAPNO and not is_auto:
        want, why = int(AREA_CHANGE_MAPNO, 0), "FMO_AREA_CHANGE_MAPNO (flat)"
    else:
        return current, "the session's current MapNo -- the label moves, you do not"
    if want not in zoneentry.VALID_MAPNOS:
        return current, (f"WARNING: REFUSED {want}: not one of the twelve type-2 "
                         f"maps that exist, and a missing map crashes the "
                         f"client -- kept {current}")
    return want, why


# Called at run time only; imported last so that import cycles resolve.
from . import zonecontrol  # noqa: E402
