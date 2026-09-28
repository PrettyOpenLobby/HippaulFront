"""The enemy squad the client runs: squad owners, positions, the fire and hit relays and kill
credit."""
import os
import struct
import time
from .deps import fmoworld


#: VERIFIED:KEY: THE ENEMY SQUAD. With
#: FMO_BATTLE_DUMMY_AI set, FMO_BATTLE_ENEMIES="<n>[:<spread>]" pops n AI
#: enemies (ids FMO_BATTLE_DUMMY's id, +1, ...) in a ring of radius <spread>
#: world units round the drop point. ONE squad per battle room, ONE OWNER:
#: the first pilot in runs the brains (POP body+0x2C = its own battle id);
#: every other pilot gets the same ids as network copies (body+0x2C = the
#: owner's alias on their client, so 1340 = 2 and no brain) and the owner's
#: movement / fire / damage records (cmd 23 / 24 / 30 / 29) are relayed to
#: them. A squad lives for the owner's sortie (battle_state granted_at); if
#: the owner leaves the room the next pilot to pop starts a fresh one.
#: WARNING: All static. The ring spread is a guess at world units (the referee's
#: 40 was never measured either).
BATTLE_ENEMIES_SPEC = os.environ.get("FMO_BATTLE_ENEMIES", "").strip() or "1"


def parse_battle_enemies(spec):
    """'3:40' -> (3, 40.0); '3' -> (3, 30.0). n is clamped to 1..8. An
    optional third field is the gap between enemies in the line (squad_gap)."""
    parts = (spec or "1").split(":")
    try:
        n = int(parts[0], 0)
        spread = float(parts[1]) if len(parts) > 1 and parts[1] else 30.0
        if len(parts) > 2 and parts[2]:
            float(parts[2])
    except ValueError:
        raise ValueError(f"FMO_BATTLE_ENEMIES={spec!r}: want <n>[:<distance>[:<gap>]]")
    return max(1, min(8, n)), spread


def squad_gap(spec=None):
    """The line's gap from FMO_BATTLE_ENEMIES' third field, default 40."""
    parts = ((BATTLE_ENEMIES_SPEC if spec is None else spec) or "").split(":")
    try:
        return float(parts[2]) if len(parts) > 2 and parts[2] else 40.0
    except ValueError:
        return 40.0


try:
    BATTLE_ENEMIES = parse_battle_enemies(BATTLE_ENEMIES_SPEC)
    _BATTLE_ENEMIES_ERR = ""
except ValueError as _e:
    BATTLE_ENEMIES, _BATTLE_ENEMIES_ERR = (1, 30.0), str(_e)
#: FMO_BATTLE_FIRE_RELAY: '1' (default) = a pilot's own FIRE (cmd 30) is
#: relayed to every other pilot in the battle on the shooter's alias stream,
#: with the record's source id (+0x08) rewritten to that alias. Under SE's
#: P2P model a client is hurt by resolving an INCOMING shot itself, and an
#: owner's client resolves hits on the enemies it runs -- so without this no
#: pilot can hurt another pilot, and a non-owner cannot hurt the squad.
BATTLE_FIRE_RELAY = os.environ.get("FMO_BATTLE_FIRE_RELAY", "1") not in ("0", "")
#: {room key: squad dict}; see battle_squad_for.
BATTLE_SQUADS = {}
#: host -> when its BATTLE stream last verified. WARNING: during a battle
#: the client keeps its LOBBY stream running on the same socket, our one
#: channel per address flips between the two keys, and every flip back to the
#: lobby key re-popped the 12-NPC lobby cast (every ~5 s). Those entities
#: landed in the battle's unit list (the review file recorded 18 units for a
#: 4-unit fight) and their name tags flashed over the battlefield. The lobby
#: cast is held while the battle stream is live (lobby_cast_paused).
#: WARNING: Keyed by PLAYER (chan_bkey), not address: live 09-27 22:21Z the Deck's
#: battle held the lobby cast for the PC on the same router, which entered a
#: lobby with no NPCs until 15 s after the Deck withdrew.
BATTLE_SEEN = {}
BATTLE_CAST_HOLD = 15.0


def lobby_cast_paused(host, now=None):
    return (now or time.time()) - BATTLE_SEEN.get(host, 0) < BATTLE_CAST_HOLD
#: cmd ids the squad owner's client sends about the units it runs.
CMD_BM_MOVE_ONE, CMD_BM_MOVE_BATCH, CMD_BM_FIRE = 23, 24, 30


def battle_room_key(ip):
    """The room a battle channel of `ip` stands in, as room_mates groups it."""
    return (rooms.WORLD_MAPS.get(ip), rooms.WORLD_ZONES.get(ip),
            move.WORLD_PLACES.get(ip) if move.PLACES else None)


def squad_positions(base, n, spread, gap=40.0):
    """n drop points in a LINE ABREAST `spread` world units out along +x from
    `base` (x, y, z[, w]), `gap` apart along z, in the same tuple shape. Pure.
    WARNING: NOT a ring: a ring round the pilot put each enemy in the
    others' line of fire -- a shot takes the NEAREST unit on its ray -- and
    two of the three were killed by their own side. From one side they all
    fire the same way."""
    base = tuple(base) if base else (0.0, 0.0, 0.0, 0.0)
    out = []
    for i in range(n):
        p = list(base)
        p[0] = float(base[0]) + spread
        p[2] = float(base[2]) + (i - (n - 1) / 2.0) * gap
        out.append(tuple(p))
    return out


def battle_squad_for(chan, base, nation, parts, now=None, mates=None):
    """(squad, owner channel or None) for the room `chan` stands in. Creates a
    squad owned by `chan`'s host when there is none, when its owner has left
    the room, or when the owner's sortie is not the one it was made for."""
    ip = referee.chan_bkey(chan)
    key = worldchannel.chan_where(chan)          # the room THIS channel stands in
    sq = BATTLE_SQUADS.get(key)
    mates = [o for o in (rooms.room_mates(chan) if mates is None else mates)
             if rooms._is_battle_chan(o)]
    owner_chan = None
    if sq is not None:
        granted = (referee.BATTLE_STATE.get(sq["owner"]) or {}).get("granted_at")
        if sq["owner"] != ip:
            owner_chan = next((o for o in mates if referee.chan_bkey(o) == sq["owner"]), None)
        if (sq["owner"] != ip and owner_chan is None) or granted != sq["granted"]:
            sq = None
    if sq is None:
        n, spread = BATTLE_ENEMIES
        uid0 = battlepop.BATTLE_DUMMY[0] if battlepop.BATTLE_DUMMY else 0x2222
        sq = {"owner": ip, "ids": [uid0 + i for i in range(n)],
              "pos": squad_positions(base, n, spread, squad_gap()),
              "dead": set(), "last_hit": {},
              "granted": (referee.BATTLE_STATE.get(ip) or {}).get("granted_at"),
              "nation": nation, "parts": parts, "made": now or time.time()}
        BATTLE_SQUADS[key] = sq
    return sq, owner_chan


def squad_owner_uid(chan, mine, owner_chan):
    """The POP body+0x2C owner for a squad unit on `chan`: the id the self-POP
    used when this client runs the brains, else the owner's alias here.

    With wire ids the self-POP uses chan.self_unit() (the character id,
    0x1001 and up). Stamping FMO_UDP_POP_BATTLE's fixed id 1 here left the
    client owning no enemy: every one arrived as a network copy with no
    brain, which never moved or fired, and the pilot's hits on it were
    skipped."""
    if mine:
        return chan.self_unit() or 0
    return chan.alias_for(owner_chan.addr) if owner_chan else 0


def move_state_len(state, at=0):
    """Byte length of one motion-state record starting at `at` (decoder
    0x6104C2F0), or None if it runs short."""
    if at + 2 > len(state):
        return None
    f = state[at + 1]
    n = (8 + 6 * bool(f & 0x01) + 2 * bool(f & 0x02) + 4 * bool(f & 0x04)
         + 4 * bool(f & 0x08) + 2 * bool(f & 0x10) + 6 * bool(f & 0x20)
         + 12 * bool(f & 0x40))
    return n if at + n <= len(state) else None


def squad_batch_filter(body, keep):
    """A cmd-24 batch (u16 n, then n x {u32 id, state}) with only the entries
    whose id is in `keep`, or None when none is kept or it does not parse."""
    if len(body) < 2:
        return None
    n = struct.unpack_from("<H", body, 0)[0]
    at, out = 2, []
    for _ in range(n):
        if at + 4 > len(body):
            return None
        uid = struct.unpack_from("<I", body, at)[0]
        ln = move_state_len(body, at + 4)
        if ln is None:
            return None
        if uid in keep:
            out.append(body[at:at + 4 + ln])
        at += 4 + ln
    if not out:
        return None
    return struct.pack("<H", len(out)) + b"".join(out)


#: KEY: cmd 43 = THE PER-SHOT HIT LIST (static 2026-09-27, the missing half of
#: combat). The fire processor 0x61058CB0 ray-tests each shot (0x6104A460),
#: lists the units it hit and SENDS the list as cmd 43 (0x6105A3AC) -- it
#: never applies a gun/melee hit itself. Damage happens only when a cmd 43
#: comes IN (receiver 0x611EEEA0, case 43 of 0x611EF2E0): each entry's target
#: is looked up, skipped if it is a network copy (1340 == 2), otherwise the
#: hit is built (0x611F0CD0), applied (0x611F1120 -> 0x61060EE0) and reported
#: as cmd 42/29. In retail every peer got the shooter's list and each applied
#: the hits on the units IT owns; we dropped it, so nothing ever took damage
#: (one test battle: 148 shots, zero cmd 29/42). Body: u8 1, u8 n, u8 weapon slot
#: (never rewrite: 0x611EEEF4 derefs NULL out of range), u8 type; then n x
#: {u32 target, u16 damage, u8 angle, u8 part (0x80 = guard)}. Header +0x08 =
#: the shooter. FMO_BATTLE_HIT_ECHO=0 turns the echo off.
CMD_BM_HITLIST = 43
BATTLE_HIT_ECHO = os.environ.get("FMO_BATTLE_HIT_ECHO", "1") not in ("0", "")


def parse_hitlist(body):
    """{'slot', 'type', 'hits': [(target, damage, angle, part)]} or None."""
    if len(body) < 4 or body[0] != 1:
        return None
    n, out = body[1], []
    for i in range(n):
        at = 4 + i * 8
        if at + 8 > len(body):
            return None
        t, d = struct.unpack_from("<IH", body, at)
        out.append((t, d, body[at + 6], body[at + 7]))
    return {"slot": body[2], "type": body[3], "hits": out}


def id_for_viewer(uid, sender, viewer):
    """The id `viewer`'s client knows the unit `sender`'s client calls `uid`
    by: the sender's own unit -> the viewer's alias for the sender; the
    sender's alias for some pilot X -> the viewer's own id when X is the
    viewer, else the viewer's alias for X; anything else (squad ids, NPCs) is
    the same number on every client."""
    if uid == sender.self_unit():
        return viewer.alias_for(sender.addr)
    for other_addr, a in getattr(sender, "alias_of", {}).items():
        if a == uid:
            return (viewer.self_unit() if other_addr == viewer.addr
                    else viewer.alias_for(other_addr))
    return uid


def hitlist_for_viewer(body, sender, viewer):
    """A cmd-43 body with every target id rewritten for `viewer`."""
    b = bytearray(body)
    for i in range(b[1] if len(b) >= 2 else 0):
        at = 4 + i * 8
        if at + 4 > len(b):
            break
        t = struct.unpack_from("<I", b, at)[0]
        struct.pack_into("<I", b, at, id_for_viewer(t, sender, viewer) & 0xFFFFFFFF)
    return bytes(b)


def hitlist_echo(chan, addr, body, arg8):
    """Send a shooter's cmd 43 back to the shooter (self stream, verbatim) and
    to every other battle pilot in the room (ids rewritten for each), so the
    owner of each target applies the hit. Returns how many OTHER pilots got it."""
    if not BATTLE_HIT_ECHO or arg8 is None or parse_hitlist(body) is None:
        return 0
    chan.pending.append(fmoworld.record(CMD_BM_HITLIST, body, arg8=arg8))
    n = 0
    for o in rooms.room_mates(chan):
        if not rooms._is_battle_chan(o):
            continue
        o.pending.append(fmoworld.record(
            CMD_BM_HITLIST, hitlist_for_viewer(body, chan, o),
            arg8=id_for_viewer(arg8, chan, o)))
        n += 1
    return n


def squad_relay(chan, addr, cmd, body, arg8):
    """The squad OWNER's records about the units it runs, to every other
    pilot in the same squad, on their self stream, header +0x08 kept. The
    owner's records about its OWN unit are never forwarded (a receiver would
    look them up as ITS own unit). Returns how many pilots got it."""
    sq = (referee.BATTLE_STATE.get(referee.chan_bkey(chan)) or {}).get("squad")
    if not sq or sq["owner"] != referee.chan_bkey(chan):
        return 0
    ids = set(sq["ids"])
    if cmd == CMD_BM_MOVE_BATCH:
        body = squad_batch_filter(body, ids)
        if body is None:
            return 0
    elif cmd in (CMD_BM_MOVE_ONE, CMD_BM_FIRE):
        if arg8 not in ids:
            return 0
    elif cmd == fmoworld.CMD_BM_HIT:
        h = fmoworld.parse_hit(body)
        if not h or h["target"] not in ids:
            return 0
    else:
        return 0
    rec = fmoworld.record(cmd, body, arg8=arg8)
    n = 0
    for o in rooms.room_mates(chan):
        if rooms._is_battle_chan(o) and (referee.BATTLE_STATE.get(referee.chan_bkey(o)) or {}).get("squad") is sq:
            o.pending.append(rec)
            n += 1
    return n


def fire_relay(chan, addr, body, arg8):
    """A pilot's OWN shot (cmd 30 naming its own unit) to every other battle
    pilot in the room, on the shooter's alias stream there, with +0x08
    rewritten to that alias. Returns how many got it."""
    if not BATTLE_FIRE_RELAY or arg8 != chan.self_unit():
        return 0
    n = 0
    for o in rooms.room_mates(chan):
        if not rooms._is_battle_chan(o):
            continue
        # PEER LINK: once the shooter's link to this mate is up, its client
        # sends the shot on that link and peer_link_serve relays it -- relaying
        # it here too would fire every shot twice.
        _mine = getattr(chan, "remotes", {}).get(
            getattr(chan, "alias_of", {}).get(o.addr))
        if room.PEER_LINK and _mine is not None and getattr(_mine, "linked", False):
            continue
        alias = o.alias_for(chan.addr)
        rs = o.remotes.get(alias)
        if rs is None or not rs.popped:
            continue
        rs.pending.append(fmoworld.record(CMD_BM_FIRE, body, arg8=alias))
        n += 1
    return n


def squad_note_hits(chan, body, arg8):
    """Remember who last HIT each squad unit, from a cmd-43 hit list: a squad
    id (an enemy hit its own side) or the host whose pilot fired. Returns
    [(target, shooter)] noted."""
    sq = (referee.BATTLE_STATE.get(referee.chan_bkey(chan)) or {}).get("squad")
    hl = parse_hitlist(body)
    if not sq or not hl or arg8 is None:
        return []
    ids = set(sq["ids"])
    if arg8 in ids:
        who = ("npc", arg8)
    elif arg8 == chan.self_unit():
        who = ("host", referee.chan_bkey(chan))
    else:
        who = next((("host", referee.chan_bkey(groupchannel.WORLD_PEERS.get(a)) or a[0])
                    for a, al in getattr(chan, "alias_of", {}).items()
                    if al == arg8), ("npc", arg8))
    out = []
    for t, _d, _a, _p in hl["hits"]:
        if t in ids:
            sq.setdefault("last_hit", {})[t] = who
            out.append((t, who))
    return out


def squad_credit_kill(sq, target, reporter, now=None, chans=None):
    """The owner reported squad unit `target` DIED: mark it dead ONCE and
    credit the kill. KEY: The hit list names the shooter (squad_note_hits): a
    pilot's hit is that pilot's kill; an enemy's hit is FRIENDLY FIRE and pays
    nobody (in one test battle two of three enemies were killed by their own side
    and the old "whoever fired last" rule paid the pilot for both). Only when
    no hit was seen does it fall back to the pilot who fired most recently.
    Returns the credited host, or None (already dead, or friendly fire)."""
    if target in sq["dead"]:
        return None
    sq["dead"].add(target)
    now = now or time.time()
    who = (sq.get("last_hit") or {}).get(target)
    if who and who[0] == "npc":
        sq.setdefault("friendly_fire", []).append((target, who[1]))
        return None
    if who and who[0] == "host":
        referee.battle_state(who[1]).setdefault("kills", []).append((target, now))
        return who[1]
    pool = [reporter] + [o for o in (rooms.room_mates(reporter) if chans is None else chans)
                         if rooms._is_battle_chan(o)]
    best = None
    for o in pool:
        t = getattr(o, "last_fire", None)
        if t is None or now - t > rooms.BATTLE_KILL_WINDOW:
            continue
        if best is None or t > best.last_fire:
            best = o
    host = referee.chan_bkey(best or reporter)
    referee.battle_state(host).setdefault("kills", []).append((target, now))
    return host


# Called at run time only; imported last so that import cycles resolve.
from . import battlepop, groupchannel, move, referee, room, rooms, worldchannel  # noqa: E402
