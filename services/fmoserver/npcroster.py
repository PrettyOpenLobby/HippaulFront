"""Which cast a zone pops: layout bands, per-host rosters, NPC POP records and NPC moves."""
import os
from .deps import fmolayout, fmoworld
from .wirelog import log


def layout_band(band):
    """(roster, names) from the layout file for `band`, or None when the
    file has no entry for it. A file that cannot be read is logged ONCE per
    distinct error and treated as absent -- the env rosters keep serving; a
    half-written file must never cost the lobby its cast."""
    if npccast.NPC_LAYOUT is None:
        return None
    try:
        r = npccast.NPC_LAYOUT.roster(band, npccast.FACE_SIGN)
    except (ValueError, OSError) as e:
        m = f"{type(e).__name__}: {e}"
        if npccast._LAYOUT_ERR[0] != m:
            npccast._LAYOUT_ERR[0] = m
            log(f"WARNING: NPC layout file {npccast.NPC_LAYOUT.path} is unreadable ({m}) -- "
                f"serving the env rosters until it is fixed")
        return None
    if npccast._LAYOUT_ERR[0] is not None:
        log(f"NPC layout file {npccast.NPC_LAYOUT.path} reads again")
        npccast._LAYOUT_ERR[0] = None
    return r


def npc_cast_configured():
    """Is there any lobby cast to pop -- an env HQ roster, or any band in
    the layout file. The first-pop gate; re-asked per datagram until it pops,
    so the first thing placed in the editor is popped without a restart."""
    if npccast.POP_NPC:
        return True
    return any(layout_band(b) for b in (fmolayout.BAND_IDS if fmolayout else ()))


def band_rosters():
    """kind -> (band name, O.C.U. (roster, names), U.S.N. (roster, names),
    source). Built on every call from the LIVE globals: the selftest (and a
    probe) reassigns POP_NPC, and a table captured at import would keep
    popping the roster that was current then. The layout file, when it carries
    a band, replaces that band's entry here -- and only that band's."""
    hq = ("HQ", (npccast.POP_NPC, npccast.POP_NPC_NAMES), (npccast.POP_NPC_USN, npccast.POP_NPC_USN_NAMES),
          "FMO_UDP_POP_NPC" + (" / FMO_UDP_POP_NPC_USN" if npccast.POP_NPC_USN_SPEC
                               else " (U.S.N. by SE's E060 pairing)"))
    occ = ("occupation", (npccast.POP_NPC_OCC, npccast.POP_NPC_OCC_NAMES),
           (npccast.POP_NPC_OCC_USN, npccast.POP_NPC_OCC_USN_NAMES),
           "FMO_UDP_POP_NPC_OCC" if npccast.POP_NPC_OCC_SPEC else
           "derived from FMO_UDP_POP_NPC (Fuller/Knox at the briefing key -- an assumption)")
    fz = ("frontline", (npccast.POP_NPC_FZ, npccast.POP_NPC_FZ_NAMES),
          (npccast.POP_NPC_FZ_USN, npccast.POP_NPC_FZ_USN_NAMES),
          "FMO_UDP_POP_NPC_FZ" if npccast.POP_NPC_FZ_SPEC else
          "derived from FMO_UDP_POP_NPC (D07's cast: Dirac/O'Brien, Schechner/Taft)")
    col = ("coliseum", (npccast.POP_NPC_COL, npccast.POP_NPC_COL_NAMES),
           (npccast.POP_NPC_COL_USN, npccast.POP_NPC_COL_USN_NAMES),
           "FMO_UDP_POP_NPC_COL" if npccast.POP_NPC_COL_SPEC else
           "the Coliseum's own catalogue on the tougi script's tier marks (desk-to-mark is a labelled guess)")
    bands = {"hq": hq, "occ": occ, "fz": fz, "col": col}
    if npccast.NPC_LAYOUT is not None:
        _ck = {}
        for _b in fmolayout.BAND_IDS:
            try:
                _ck.update(npccast.NPC_LAYOUT.ckinds(_b))
            except (ValueError, OSError):
                pass
        npccast.NPC_CKIND_OVERRIDE.clear()
        npccast.NPC_CKIND_OVERRIDE.update(_ck)
    for _b in bands:
        _lr = layout_band(_b)
        if _lr is not None:
            bands[_b] = (bands[_b][0], _lr, npccast.usn_roster(*_lr),
                         f"the layout file {npccast.NPC_LAYOUT.path} ({len(_lr[0])} rows, "
                         f"the lobby NPC editor; U.S.N. by SE's E060 pairing)")
    hq, occ, fz, col = bands["hq"], bands["occ"], bands["fz"], bands["col"]
    return {1: hq, 3: hq, 2: occ, 4: occ, 5: fz, 6: col}


def roster_for(host_ip):
    """(roster, names, why) to pop for this host: the band of the zone its
    last 0x0153 granted (WORLD_ZONES), the faction of its pilot. A place
    that is not a lobby (FMO_PLACES) gets no cast: the counters belong to
    the lobby, and nothing measured says who stands in a room or hangar."""
    if move.PLACES:
        _pl = move.WORLD_PLACES.get(host_ip)
        if _pl is not None and _pl[1] != 0:
            # VERIFIED: 2026-09-11: a PLACE band in the layout file (the lobby NPC
            # editor's Room / Briefing Room / Room B / Room C / Hangar tabs)
            # is the cast for that room kind -- SE's tables carry room and
            # hangar staff keys; whether a popped body renders and talks in a
            # room is the live test the editor exists to run. No band = no
            # cast, exactly as before.
            _pb = fmolayout.PLACE_BANDS.get(_pl[1]) if fmolayout else None
            _plr = layout_band(_pb) if _pb else None
            _bays = hangar.hangar_resident_units(host_ip, _pl)
            if _plr is not None and _bays:
                # KEY: A LAYOUT ROW FOR A BAY KEY IS A POSITION, NOT A BODY.
                # It used to replace the bay unit outright, which made the
                # editor useless for the one thing it is wanted for here: the
                # placer can only build a person or a terminal, so dragging the
                # parked wanzer into place turned it back into a clone of the
                # player. The row now supplies x/y/z AND FACING while the server
                # keeps the unit it generated -- the wanzer class and the
                # dressing from the pilot's own setup. So a drag is still live
                # and restart-free, and it cannot cost you the wanzer.
                # WARNING: Only for keys a bay unit actually exists for: a row for an
                # unused setup's key is left alone and pops as itself, exactly
                # as it did before.
                _bayk = {b[0] for b in _bays}
                _rowpos = {e[0]: e[2] for e in _plr[0] if e[0] in _bayk}
                if _rowpos:
                    _bays = [(b[0], b[1], _rowpos.get(b[0], b[2]), b[3])
                             for b in _bays]
                    _plr = ([e for e in _plr[0] if e[0] not in _rowpos],
                            _plr[1])
            if _plr is not None or _bays:
                _plr = _plr if _plr is not None else ([], {})
                if zoneentry.NATION_PER_CHARACTER and popnation.pop_nation_for(host_ip)[0] == 2:
                    _plr = npccast.usn_roster(*_plr)
                _have = {e[0] for e in _plr[0]}
                _miss = [hex(k) for k in hangar.HANGAR_REQUIRED_KEYS if k not in _have] if _pl[1] == 5 else []
                return (list(_plr[0]) + _bays, _plr[1],
                        f"{move.place_name(_pl)}: the layout file's {_pb} band ({len(_plr[0])} rows)"
                        + (f" + {len(_bays)} bay unit(s) for the owner's wanzer setups" if _bays else "")
                        + (f"; WARNING: the owner's consoles stay SHUT until {_miss} are placed "
                           f"(0x61002BC0)" if _bays and _miss else ""))
            return [], {}, (f"no cast: {move.place_name(_pl)} is not a lobby "
                            f"(rooms/hangars have no measured staff -- place "
                            f"someone in the editor's {_pb or 'place'} tab)")
    zone, kind = rooms.zone_of(host_ip)
    BAND_ROSTERS = band_rosters()
    name, ocu, usn, src = BAND_ROSTERS.get(kind, BAND_ROSTERS[1])
    if kind not in BAND_ROSTERS:
        name = f"HQ roster for kind {kind} (no band roster of its own)"
    band = f"{name} band (zone {zone}): {src}"
    if zoneentry.NATION_PER_CHARACTER:
        n, nsrc = popnation.pop_nation_for(host_ip)
        if n == 2:
            return usn[0], usn[1], f"U.S.N. {band}; nation from {nsrc}"
        if n == 1:
            return ocu[0], ocu[1], f"O.C.U. {band}; nation from {nsrc}"
    return ocu[0], ocu[1], f"O.C.U. {band} (FMO_UDP_POP_NPC)"

#: KEY: THE NPC SOURCES' client_kind (body+0x00) -- `FMO_UDP_POP_NPC_CLIENT_KIND`.
#: Empty (default) = inherit the self-POP's kind (0 today), which is what has
#: shipped all along. A 2026-09-05 live look at the settled lobby: the
#: eight sources DRAW, but as clones of the player that the game treats as
#: PLAYERS. The creator 0x611EADC0 explains it -- body+0x00 in {0,2,3} allocates
#: a PEER client (0x1E21 B, the per-player connection object) before the
#: entity; **kind 1 allocates NO peer** (0x611EAE86 -> 0x611EAF9A) and builds
#: the entity through 0x611EAFF6 with class argument 0 instead of the player's
#: 1. So 1 is the one wire value that yields an entity without a player behind
#: it. Whether the client then renders it as a human and lets you talk to it is
#: the live question this knob exists to ask -- with ONE source, in the settled
#: lobby, ready to disarm (a POP-body change is world-entry-critical).
#: fmoworld.record_pop refuses anything outside {0,1,2,3}.
_npck = os.environ.get("FMO_UDP_POP_NPC_CLIENT_KIND", "").strip()
POP_NPC_CLIENT_KIND = int(_npck, 0) if _npck else None

#: KEY: FMO_UDP_POP_NPC_TARGETABLE -- set bit 0x10 of body+0x48 on every NPC pop.
#: Static 2026-09-05 (fmoworld.POP_TARGETABLE):
#: the lobby's "Select target" list (0x611824D6, opened by `/target` with no
#: argument) lists an entity only if `entity+0x18F & 0x10`, the bulk copy of
#: body byte 0x48 -- a byte we have always sent as zero, for NPCs AND players.
#: This is the first thing an NPC needs before the client can target it at
#: all; what the client does with an NPC target afterwards is unread. Default
#: off = today's bytes. NPC records only; the self-POP is untouched.
_npct = os.environ.get("FMO_UDP_POP_NPC_TARGETABLE", "").strip()
POP_NPC_TARGETABLE = _npct not in ("", "0")
#: KEY: FMO_UDP_POP_NAMES -- the OVERHEAD NAME TAG (static 2026-09-12, asked
#: live: "neither NPCs nor players have name text"). The drawer
#: 0x611E8440 looks the entity up, skips unless `entity+0x18F & 0x01`, skips
#: if the `/names off` global ([globals+0x1F0] & 2) is set, projects the
#: entity's position plus its height (+0x18B) to the screen and prints
#: "%s %s" from the two 17-byte names (+0x10C/+0x11D). entity+0x18F is body
#: byte 0x48 -- the byte whose bit 0x10 is the target-list gate, sent as ZERO
#: on every POP this server ever built. SE's `/names on|off` (guide/textcmd)
#: applies to "PC・WAP", so tags over players and wanzers are the normal look.
#: '1' (default) = OR bit 0x01 into body+0x48 on EVERY record (NPCs, the self,
#: remote players, terminals). '0' = today's bytes.
POP_NAMES = (os.environ.get("FMO_UDP_POP_NAMES", "").strip() or "1") != "0"
if fmoworld is not None:
    fmoworld.NAMETAG = POP_NAMES


def _bay_wanzer_parts(uid, utype, host_ip):
    """([(idx, kind, id)], why) for a HANGAR BAY unit popped as a WANZER, or
    (None, why) when this unit is not one.

    KEY: SE's hangar shows the pilot's PARKED WANZER, and the setup screen builds
    its display by copying this very unit's body (0x611009C6 walks
    0x82080000..07). Popped as UnitType 4 it is a clone of the player standing
    in their own hangar -- which is what was seen live on 2026-09-11. Popped
    as a wanzer class it needs the eleven part records at body+0x8C, and those
    come from THE SAME source the battle self-pop uses, so the hangar and the
    sortie cannot show different machines (the player database's rule).

    WARNING: UnitTypes 4 and 30 never call the part dresser, so they get None here --
    record_pop refuses the pair outright, which is the guard, not this."""
    if not (0x82080000 <= uid <= 0x82080007):
        return None, "not a bay key"
    if utype not in fmoworld.POP_UNITTYPES_MAIN:
        return None, f"UnitType {utype} does not read parts"
    if not host_ip:
        return None, "no host for the store lookup"
    parts, src = popparts.pop_parts_for(host_ip)
    return (parts or None), src


def _npc_pop_record(uid, utype, npos, cat, chan, self_pos, names_map=None,
                    host_ip=None):
    """One NPC cmd-7 record for an FMO_UDP_POP_NPC entry -- shared by the first
    pop and the RELOOK re-pop so the two cannot drift apart. Raises ValueError
    exactly where fmoworld.record_pop does (the callers log and skip).

    Body: the proven-valid SELF-POP body verbatim (a stub body black-screens
    the world, seen 2026-09-04) with the id, position and client_kind
    swapped; then, per entry, the catalogue dress (`#typecode`) replacing the
    player's names and look, and the targetable bit (FMO_UDP_POP_NPC_TARGETABLE).
    A non-human UnitType gets a bare body, as before."""
    p = npos if npos is not None else self_pos
    ck = {} if POP_NPC_CLIENT_KIND is None else {"client_kind": POP_NPC_CLIENT_KIND}
    if uid in npccast.NPC_CKIND_OVERRIDE:        # the editor's per-row name-tag test
        ck = {"client_kind": npccast.NPC_CKIND_OVERRIDE[uid]}
    extra = {}
    if POP_NPC_TARGETABLE:
        extra[fmoworld.POP_TARGETABLE] = bytes([fmoworld.POP_TARGETABLE_BIT])
    names = {}
    if cat is not None:
        n1, n2, cextra = fmoworld.npc_catalogue_dress(cat)
        names = {"name1": n1, "name2": n2}
        extra.update(cextra)
    nm = npccast.POP_NPC_NAMES if names_map is None else names_map
    if uid in nm:
        names = {"name1": nm[uid][0], "name2": nm[uid][1]}
    # KEY: THE STREAM KEY. body+0x08 is the Blowfish key the client gives this
    # unit's OWN peer stream (fmoworld POP_CLIENT_BLOB). With it set to our
    # channel key, a datagram stamped with the NPC's id verifies -- and a
    # cmd 240 on that stream MOVES the NPC (npc_move_flush). Same rule as the
    # room relay's ROOM_PEER_KEY; an unkeyed blob is why relayed players
    # "appeared and never moved" on 2026-08-26.
    ckey = {"client_key": chan.key} if (room.ROOM_PEER_KEY and chan.key) else {}
    if utype == 4 and chan.pop_args:
        # `parts` is a WANZER field and this arm forces UnitType 4, which never
        # calls the part dresser -- record_pop refuses the pair on purpose, so
        # drop it here rather than let a future battle-side NPC pop raise.
        args = dict(chan.pop_args, pos=p, unit_type=4, parts=None, **ck, **ckey)
        args.update(names)
        # the catalogue IS the look; the player's look block would be
        # overwritten by `extra` anyway, so do not apply it at all when dressing
        look = None if cat is not None else chan.type4_look
        return fmoworld.record_pop(uid, look=look, extra=extra or None, **args)
    # THE PARKED WANZER. A wanzer-class bay unit gets the full self-POP body
    # (a stub body black-screened the world once already) with the setup's
    # parts, and NO look/dress -- the parts ARE the look. model_flags is
    # dropped because body+0x8E is part record 0's kind byte; record_pop
    # refuses the pair rather than silently equipping a flag as a part.
    _bp, _bwhy = _bay_wanzer_parts(uid, utype, host_ip)
    if _bp and chan.pop_args:
        args = dict(chan.pop_args, pos=p, unit_type=utype, parts=_bp,
                    **ck, **ckey)
        args.pop("model_flags", None)
        args.update(names)
        return fmoworld.record_pop(uid, extra=extra or None, **args)
    return fmoworld.record_pop(uid, unit_type=utype, pos=p,
                               extra=extra or None, **names, **ck, **ckey)


def npc_moves(prev_roster, roster):
    """[(uid, pos)] for every roster entry whose POSITION or FACING changed
    since `prev_roster` (same shape, [(uid, utype, pos, cat)]); a new key or a
    key with no position is not a move. Pure -- the selftest drives it."""
    before = {e[0]: e[2] for e in (prev_roster or [])}
    out = []
    for uid, _utype, pos, _cat in roster:
        if pos is None or uid not in before or before[uid] is None:
            continue
        if tuple(pos) != tuple(before[uid]):
            out.append((uid, tuple(pos)))
    return out


def npc_move_queue(chan, moves):
    """Queue a cmd 240 for each moved NPC on that NPC's OWN alias stream
    (chan.npc_remotes), the mechanism that walks relayed players. A present
    key's re-pop rebuilds the body but never re-copies entity+0x44, which is
    why an editor move used to show only after re-entry (LIVE 2026-09-11,
    Ann Fuller). Returns how many were queued; refused coordinates are
    logged and skipped."""
    n = 0
    for uid, pos in moves:
        rs = chan.npc_remotes.get(uid)
        if rs is None:
            rs = chan.npc_remotes[uid] = worldchannel.RemoteStream(uid, None)
            rs.popped = True
        try:
            rs.pending.append(fmoworld.record_move(tuple(pos[:3]),
                                                   pos[3] if len(pos) > 3 else 0.0, flags=6))
        except ValueError as e:
            log(f"[udp {chan.addr[0]}:{chan.addr[1]}] WARNING: NPC MOVE for {uid:#x} refused: {e}")
            continue
        n += 1
        log(f"[udp {chan.addr[0]}:{chan.addr[1]}] -> NPC MOVE queued: cmd {fmoworld.CMD_MOVE} "
            f"for {uid:#x} to ({pos[0]:.2f}, {pos[1]:.2f}, {pos[2]:.2f}) rot "
            f"{pos[3] if len(pos) > 3 else 0.0:+.3f} on ITS OWN alias stream -- the client "
            f"applies a cmd 240 to the unit the datagram is stamped with. If this stream is "
            f"never acked, the client has no peer for that id (client_kind?) and the "
            f"editor's per-row client kind 0 is the A/B.")
    return n


# Called at run time only; imported last so that import cycles resolve.
from . import (  # noqa: E402
    hangar, move, npccast, popnation, popparts, room, rooms, worldchannel, zoneentry,
)
