"""Spoils: the battle group's loot window after a win (group cmds 214/215 out, 216/217/219 in)
and the items it grants (0x016B)."""
import random
import struct
import threading
import time
from .knobs import _env_int
from .wirelog import log


# --------------------------------------------------------------------------- #
# THE WIRE (static 2026-09-30, fmodis; every address re-read for this module)
# --------------------------------------------------------------------------- #
#: KEY: SPOILS RIDE THE BATTLE-GROUP CHANNEL, not the lobby TCP. CFmoGroup's
#: record switch 0x611E5980 (cmd - 7 through the byte table 0x611E5BD4 into
#: the jump table 0x611E5B98) has two loot arms, and like cmd 191 both run
#: only for the peer whose UnitID (+0x10) equals G+0x2C -- the SELF stream,
#: exactly where group_blob_update already puts its cmd 191:
#:   cmd 214 (0xD6) OPEN, 0x611E4C90 ("Got %d items as spoils"):
#:       body+0x00 u32 count -> G+0x4C
#:       body+0x08 32 u32    -> G+0x50 (rep movsd 0x20), one per drop:
#:                           low u16 = item id, byte 2 = item kind (the
#:                           watcher 0x611A01A7 splits it exactly so and
#:                           names it through 0x61175840(kind, id))
#:       starts the window's 120 s timer: G+0xD4 = GetTickCount + 0x1D4C0,
#:       and G+0xD8 = 2 (1 when the count is 0).
#:   cmd 215 (0xD7) CLOSE, 0x611E4D20 ("Loot shared"): G+0x4C = 0,
#:       G+0xD4 = 0, G+0xD8 = 3 -- the window closes. It reads no body.
CMD_LOOT_OPEN, CMD_LOOT_CLOSE = 0xD6, 0xD7
LOOT_OPEN_COUNT, LOOT_OPEN_ITEMS = 0x00, 0x08
LOOT_MAX = 32
LOOT_OPEN_LEN = LOOT_OPEN_ITEMS + 4 * LOOT_MAX         # 0x88
#: The client's own window time (0x611E4CD6 `add eax, 0x1D4C0`).
LOOT_WINDOW_S = 120
#: The CHOICES, client -> server, all through 0x611E2750(0, cmd, buf, len, 0)
#: -- peer 0 = the self id [G+0x2C], so they arrive on the self stream too.
#: The row state byte (0x61239C46, 11-byte rows from +0x34) is 0 Need,
#: 1 Want (every row starts at 1, 0x611A01C5), 2 Pass; clicking cycles it
#: Want -> Need -> Pass -> Want and sends:
#:   Need  0x611E5050: cmd 0xD8 {u32 idx, u32 0} (and sets G+0xD0 = 1)
#:   Want  0x611E5090: cmd 0xD8 {u32 idx, u32 1}
#:   Pass  0x611E5020: cmd 0xD9 {u32 idx, u32 0}
#:   Pass all 0x611E50F0: cmd 0xD9 {0x20, 0}; Want all 0x611E5120: {0x21, 0}
#:   OK    0x611E50D0: cmd 0xDB {u32 0}, 4 bytes (from the window's OK,
#:         0x611A05AA, and from 0x61239A6A)
#: One Need per drop set is the CLIENT's rule (0x61239C83: a new Need turns
#: every other Need back to Want and sends a Want for each); we enforce it
#: again, because a server must not trust it.
CMD_LOOT_WANT, CMD_LOOT_PASS, CMD_LOOT_OK = 0xD8, 0xD9, 0xDB
NEED, WANT, PASS = 0, 1, 2
PASS_ALL, WANT_ALL = 0x20, 0x21
CHOICE_NAMES = {NEED: "Need", WANT: "Want", PASS: "Pass"}

#: FMO_LOOT=1 (default): a battle group that WINS gets a loot window. SE
#: (AH/F98/D92 239-245, gamesystem/job on the archived site): drops go to the
#: battle group after a battle; Need beats Want, Pass gets nothing; the window
#: closes when everyone has answered or its time is up. 0 = no spoils.
LOOT = _env_int("FMO_LOOT", "1") != 0
#: FMO_LOOT_DROPS: drops per won group battle. SE's tables and odds are not
#: in the client; OURS, capped at the window's 32.
LOOT_DROPS = max(0, min(LOOT_MAX, _env_int("FMO_LOOT_DROPS", "3")))
#: FMO_LOOT_ENEMY_PCT: percent of drops taken from the DEFEATED enemies' own
#: loadouts (fmo-npc-loadouts.tsv, the squad this battle popped); the rest
#: are any part within FMO_LOOT_BAND levels below the battle's NPC level
#: (fmo-part-levels.tsv). Both OURS.
LOOT_ENEMY_PCT = _env_int("FMO_LOOT_ENEMY_PCT", "50")
LOOT_BAND = _env_int("FMO_LOOT_BAND", "10")
#: Seconds past the client's 120 s before we close a round nobody finished
#: (ours: the client's timer runs from ITS receipt, a little after our send).
LOOT_GRACE = _env_int("FMO_LOOT_GRACE", "5")
#: How old a group battle may be and still open a round (ours).
LOOT_BATTLE_AGE = _env_int("FMO_LOOT_BATTLE_AGE", "3600")


def loot_word(kind, item_id):
    """One drop as the window stores it: id in the low u16, kind in byte 2."""
    return ((int(kind) & 0xFF) << 16) | (int(item_id) & 0xFFFF)


def loot_open_body(items):
    """The cmd 214 body for `items` [(kind, id)]: count, pad, 32 words."""
    items = list(items)[:LOOT_MAX]
    b = bytearray(LOOT_OPEN_LEN)
    struct.pack_into("<I", b, LOOT_OPEN_COUNT, len(items))
    for i, (k, iid) in enumerate(items):
        struct.pack_into("<I", b, LOOT_OPEN_ITEMS + 4 * i, loot_word(k, iid))
    return bytes(b)


def parse_loot_open(body):
    """[(kind, id)] out of a cmd 214 body (the selftest's reader)."""
    n = min(LOOT_MAX, struct.unpack_from("<I", body, LOOT_OPEN_COUNT)[0])
    return [((w >> 16) & 0xFF, w & 0xFFFF) for w in
            struct.unpack_from("<%dI" % n, body, LOOT_OPEN_ITEMS)] if n else []


def apply_choice(states, cmd, body):
    """Apply one choice record to `states` (this member's per-drop list).
    Returns a short description, or None when it is not a loot choice.
    MUTATES `states`."""
    if cmd == CMD_LOOT_OK:
        return "OK"
    if cmd not in (CMD_LOOT_WANT, CMD_LOOT_PASS) or len(body) < 8:
        return None
    idx, v = struct.unpack_from("<II", body, 0)
    if cmd == CMD_LOOT_PASS and idx in (PASS_ALL, WANT_ALL):
        new = PASS if idx == PASS_ALL else WANT
        for i in range(len(states)):
            states[i] = new
        return "Pass all" if new == PASS else "Want all"
    if idx >= len(states):
        return "index %d out of range" % idx
    if cmd == CMD_LOOT_PASS:
        states[idx] = PASS
    elif v == 0:
        # one Need per drop set (0x61239C83): any other Need becomes Want
        for i in range(len(states)):
            if states[i] == NEED:
                states[i] = WANT
        states[idx] = NEED
    else:
        states[idx] = WANT
    return "%s #%d" % (CHOICE_NAMES[states[idx]], idx)


def resolve(items, choices, rnd=None):
    """{drop index: winning account or None}. SE's rule: a random Need wins,
    else a random Want; Pass never. `choices` {account: [state per drop]}."""
    rnd = rnd or random.Random()
    out = {}
    for i in range(len(items)):
        need = sorted(a for a, st in choices.items() if i < len(st) and st[i] == NEED)
        want = sorted(a for a, st in choices.items() if i < len(st) and st[i] == WANT)
        pool = need or want
        out[i] = rnd.choice(pool) if pool else None
    return out


# --------------------------------------------------------------------------- #
# THE DROP TABLE (ours: SE's tables are not in the client)
# --------------------------------------------------------------------------- #
def load_part_names(path=None):
    """{(kind, id): (level, name)} from fmo-part-levels.tsv; {} when absent."""
    out = {}
    try:
        with open(path or battlegroups.PART_LEVELS_TSV, encoding="utf-8") as f:
            f.readline()
            for line in f:
                c = line.rstrip("\r\n").split("\t")
                if len(c) >= 3:
                    out[(int(c[0], 0), int(c[1]))] = (int(c[2]), c[3] if len(c) > 3 else "")
    except (OSError, ValueError):
        return {}
    return out


_PARTS = []


def part_names():
    if not _PARTS:
        _PARTS.append(load_part_names())
    return _PARTS[0]


def npc_only_names(rows):
    """The NPC-only set names (npc60-1, RECN1-OCU, zora_ev1_1 ...): a part
    whose master name IS a loadout's own name is the enemy's frame, never a
    garage part, so it never drops."""
    return {r.get("name") for r in rows or () if r.get("name")}


def drop_pool(level, parts=None, npc_names=(), loadouts=(), band=None):
    """(enemy pool, band pool), each [(kind, id)]. Enemy pool: the parts the
    defeated enemies wore (`loadouts`, squad rows); band pool: every part at
    level (level - band, level]. Both drop NPC-only names, unknown parts and
    kinds the item list cannot hold (inventory.VALID_ITEM_KINDS). Pure."""
    parts = part_names() if parts is None else parts
    band = LOOT_BAND if band is None else band
    npc = set(npc_names)

    def ok(k, i):
        p = parts.get((k, i))
        return (p is not None and k in inventory.VALID_ITEM_KINDS
                and p[1] not in npc)
    enemy = []
    for lo in loadouts or ():
        for _slot, k, i in (lo or {}).get("parts") or ():
            if ok(k, i) and (k, i) not in enemy:
                enemy.append((k, i))
    lvl = int(level or 0)
    near = sorted((k, i) for (k, i), (pl, _n) in parts.items()
                  if lvl - band < pl <= lvl and ok(k, i))
    return enemy, near


def draw_drops(n, enemy, near, rnd=None, enemy_pct=None):
    """`n` drops: each from the enemy pool FMO_LOOT_ENEMY_PCT percent of the
    time (when it has any), else from the level band. Pure but for `rnd`."""
    rnd = rnd or random.Random()
    pct = LOOT_ENEMY_PCT if enemy_pct is None else enemy_pct
    out = []
    for _ in range(max(0, int(n))):
        src = enemy if (enemy and (not near or rnd.randrange(100) < pct)) else near
        if not src:
            break
        out.append(rnd.choice(src))
    return out


# --------------------------------------------------------------------------- #
# ROUNDS: one per won group battle
# --------------------------------------------------------------------------- #
_lock = threading.RLock()
#: {round key: round}. A round: {"key", "gid", "items", "members", "choices"
#: {account: [state]}, "done" set, "sent" {account: channel seen mark},
#: "opened", "deadline", "closed", "winners"}.
ROUNDS = {}
#: {account: [(kind, id, round key)]} won and not yet delivered: applied on
#: the winner's own session thread at its next keepalive (loot_pushes_due),
#: the same hand-over gatetool uses, so no other thread edits its roster.
PENDING_GRANTS = {}


def group_battle_of(account, now=None, battles=None):
    """(gid, battle record) of the newest group battle `account` fought in,
    within LOOT_BATTLE_AGE, or None."""
    now = time.time() if now is None else now
    battles = battlegroups.GROUP_BATTLE if battles is None else battles
    best = None
    for gid, rec in list(battles.items()):
        if account in (rec.get("joined") or ()) and now - rec.get("at", 0) <= LOOT_BATTLE_AGE:
            if best is None or rec.get("at", 0) > best[1].get("at", 0):
                best = (gid, rec)
    return best


def round_key(gid, rec):
    return (gid, rec.get("n"), rec.get("at"))


def open_round(gid, rec, items, now=None):
    """Open the loot round for group battle `rec` of group `gid` with drops
    `items`, once: a second member's win on the same battle returns the round
    already open (or None if it has closed). None when there is nothing."""
    now = time.time() if now is None else now
    key = round_key(gid, rec)
    with _lock:
        if key in ROUNDS:
            rd = ROUNDS[key]
            return None if rd["closed"] else rd
        items = list(items)[:LOOT_MAX]
        members = list(rec.get("joined") or ())
        if not items or not members:
            return None
        rd = {"key": key, "gid": gid, "items": items, "members": members,
              "choices": {a: [WANT] * len(items) for a in members},
              "done": set(), "sent": {}, "opened": now,
              "deadline": now + LOOT_WINDOW_S + LOOT_GRACE,
              "closed": False, "winners": None}
        ROUNDS[key] = rd
    return rd


def open_rounds_for(account):
    with _lock:
        return [rd for rd in ROUNDS.values()
                if not rd["closed"] and account in rd["members"]]


def close_round(rd, why, rnd=None):
    """Resolve and close `rd`: winners' items go to PENDING_GRANTS, every
    member channel that saw the window gets a cmd 215. Returns the winners."""
    with _lock:
        if rd["closed"]:
            return rd["winners"]
        rd["closed"] = True
        rd["winners"] = resolve(rd["items"], rd["choices"], rnd)
        for i, acct in rd["winners"].items():
            if acct is not None:
                k, iid = rd["items"][i]
                PENDING_GRANTS.setdefault(acct, []).append((k, iid, rd["key"]))
        for acct in rd["members"]:
            c = _group_chan(acct)
            if c is not None and acct in rd["sent"]:
                c.pending.append(fmoworld.record(CMD_LOOT_CLOSE, bytes(4)))
    log(f"   LOOT: group {rd['gid']} round closed ({why}): "
        + ", ".join(f"#{i} kind 0x{rd['items'][i][0]:02X} id {rd['items'][i][1]} -> "
                    f"{w or 'nobody (all passed)'}" for i, w in sorted(rd["winners"].items()))
        + " -- Need beats Want, Pass gets nothing (AH/F98/D92 239-245); "
        "cmd 215 closes each window")
    return rd["winners"]


def _group_chan(account):
    """`account`'s battle-group channel, or None."""
    return next((c for k, c in list(groupchannel.WORLD_PEERS.items())
                 if isinstance(k, tuple) and len(k) == 3 and k[2] == "group"
                 and getattr(c, "account", None) == account), None)


def close_due(now=None, rnd=None):
    """Close every round whose time is up. Returns how many closed."""
    now = time.time() if now is None else now
    with _lock:
        due = [rd for rd in ROUNDS.values() if not rd["closed"] and now >= rd["deadline"]]
    for rd in due:
        close_round(rd, f"its {LOOT_WINDOW_S} s window ran out", rnd)
    return len(due)


def loot_tick(chan, got, now=None):
    """The group channel's loot half, once per datagram (datagram.py): send
    this member's open round (again after a channel restart), read its
    choices, close the round when every member has pressed OK."""
    if not LOOT or chan.key != groupchannel.GROUP_KEY or getattr(chan, "mission_type", None):
        return 0
    acct = getattr(chan, "account", None)
    # KEY: A RESTART DROPS pending AND restarts record indices (restart()
    # zeroes `seen`), so a window queued before it never arrived and our
    # dedupe index must start again. `seen` only moves forward otherwise.
    restarted = chan.seen <= getattr(chan, "loot_seen", 0)
    chan.loot_seen = chan.seen
    if restarted:
        chan.loot_acted = 0
    n = 0
    for rd in open_rounds_for(acct) if acct else ():
        mark = rd["sent"].get(acct)
        if getattr(chan, "group_popped", False) and (mark is None or restarted):
            chan.pending.append(fmoworld.record(CMD_LOOT_OPEN, loot_open_body(rd["items"])))
            rd["sent"][acct] = chan.seen
            n += 1
            log(f"[udp {chan.addr[0]}:{chan.addr[1]}] -> LOOT cmd 214 to {acct}: "
                f"{len(rd['items'])} drop(s) "
                + ", ".join(f"kind 0x{k:02X} id {i}" for k, i in rd["items"])
                + f" (group {rd['gid']}; 0x611E4C90 opens the Spoils window and "
                f"its {LOOT_WINDOW_S} s timer)")
    for j, (_off, _size, cmd, body) in enumerate((got or {}).get("records") or ()):
        if cmd not in (CMD_LOOT_WANT, CMD_LOOT_PASS, CMD_LOOT_OK):
            continue
        idx = ((got.get("from") or 0) + j) & 0xFFFF
        if idx < getattr(chan, "loot_acted", 0):
            continue                         # a resend of a record already applied
        chan.loot_acted = idx + 1
        rds = open_rounds_for(acct)
        if not rds:
            log(f"[udp {chan.addr[0]}:{chan.addr[1]}] LOOT cmd {cmd:#x} from "
                f"{acct} with no open round -- ignored")
            continue
        rd = max(rds, key=lambda r: r["opened"])
        with _lock:
            what = apply_choice(rd["choices"][acct], cmd, body)
            if cmd == CMD_LOOT_OK:
                rd["done"].add(acct)
            all_done = rd["done"] >= set(rd["members"])
        log(f"[udp {chan.addr[0]}:{chan.addr[1]}] LOOT {acct}: {what} -> "
            + " ".join(CHOICE_NAMES[s] for s in rd["choices"][acct]))
        if all_done:
            close_round(rd, "every member pressed OK")
    close_due(now)
    return n


def loot_pushes_due(sess, conn_id, now=None):
    """Called from the session's keepalive: open a round for a won group
    battle this session has just settled, close rounds whose time is up, and
    bank + push (0x016B) whatever this pilot has won."""
    if not LOOT:
        return []
    st = getattr(sess, "battle_settlement", None)
    if st is not None and st is not getattr(sess, "_loot_settled", None):
        sess._loot_settled = st
        if st.get("won"):
            try:
                sess.loot_battle_settle(True, now=now)
            except Exception as e:           # spoils must never cost the keepalive
                log(f"{sess.peer}   WARNING: LOOT: could not open a round ({e!r})")
    close_due(now)
    acct = getattr(sess, "account", None)
    with _lock:
        won = PENDING_GRANTS.pop(acct, []) if acct else []
    if not won:
        return []
    char = sess.playing_char() if charstore.CHAR_STORE else None
    if char is None:
        log(f"{sess.peer}   WARNING: LOOT: {len(won)} item(s) won but no pilot on "
            f"this connection -- kept for the next keepalive")
        with _lock:
            PENDING_GRANTS.setdefault(acct, [])[:0] = won
        return []
    recs, names = [], []
    for k, iid, _key in won:
        if len(inventory.stored_items(char)) >= inventory.INV_MAX:
            log(f"{sess.peer}   LOOT: item list full ({inventory.INV_MAX}, "
                f"0x61177B3B) -- kind 0x{k:02X} id {iid} is lost, as 8:53 says")
            continue
        lo, hi = shop.mint_serial()
        inventory.add_stored_item(char, lo | (hi << 32), iid, k, 0)
        recs.append(inventory.item_record(lo | (hi << 32), iid, k))
        names.append(f"kind 0x{k:02X} id {iid} "
                     f"({(part_names().get((k, iid)) or (0, '?'))[1]})")
    if not recs:
        return []
    try:
        sess.commit(f"spoils: {', '.join(names)}")
    except Exception as e:
        log(f"{sess.peer}   WARNING: LOOT: spoils NOT banked ({e!r})")
    log(f"{sess.peer}   -> 0x{shop.MSG_ACQUIRE_REPLY:04X} MINT push (spoils), "
        f"{len(recs)} item(s): {', '.join(names)}. The arm 0x6117ED22 appends "
        f"each (cap 400) and prints 8:2 'Obtained %s.'; battle/in-world gate "
        f"0x611734E0.")
    return [packet.build(shop.MSG_ACQUIRE_REPLY, shop.item_mint_payload(recs),
                         pushes.QUEUE_SEQ, conn_id)]


def battle_squad(keys, squads=None):
    """The newest squad one of `keys` (a battle key, an address) owned."""
    squads = squad.BATTLE_SQUADS if squads is None else squads
    best = None
    for sq in list(squads.values()):
        if sq.get("owner") in keys and (best is None or sq.get("made", 0) > best.get("made", 0)):
            best = sq
    return best


# Called at run time only; imported last so that import cycles resolve.
from .deps import fmoworld  # noqa: E402
from . import (  # noqa: E402
    battlegroups, charstore, groupchannel, inventory, packet, pushes, shop, squad,
)
