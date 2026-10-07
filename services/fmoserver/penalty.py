"""Friendly-fire penalty points and retraining: the 0x017B report, the 0x017C vote, the three
script bytes (+0x418..+0x41A) and the retraining wins."""
import struct
import time
from .knobs import _env_int
from .wirelog import log


# --------------------------------------------------------------------------- #
# KEY: THE PENALTY LOOP (static 2026-09-30: FrontMissionOnline.dll + the SCP
# scripts AH/F98/D63 (counters), AH/F98/D83 (training ground); texts D64, D84)
# --------------------------------------------------------------------------- #
#: 1. THE REPORT. 0x017B (arm 0x6117ECE4, only under the BATTLE gate
#:    0x611734E0, so it must go out BEFORE the 0x014C) copies payload +0x00
#:    (u32 count) -> lobby+0x7934 and 0x8C dwords from +0x24 -> lobby+0x7938:
#:    ten rows of 0x38 = {u32 offender id, 17B first name, 17B last name}.
#:    +0x04..+0x23 is never read. lobby+0x7934 is zeroed at the next sortie
#:    (0x6117AD87), so a battle with no friendly fire needs no push.
#: 2. THE VOTE. After the battle 0x61176EFE (count > 0) walks the rows:
#:    0x61191E80 draws systext 11:9 "It has been reported that %s.%s
#:    committed friendly fire against you ... Give penalty points?" with
#:    row+0x04 / row+0x15; YES (0x61191F53) calls 0x61174480 with row+0x00,
#:    which queues 0x017C (20 B, +0x00 = that id) and reads no reply. NO
#:    sends nothing.
#: 3. THE STATE. The 0x015A tail (0x6117E958) stores +0x418 -> lobby+0x8BE,
#:    +0x419 -> lobby+0x8BF, +0x41A -> lobby+0xE18; the 0x014A serves the
#:    same three at +0x3A, +0x3B, +0x594. Script natives read them back:
#:    E0A1 = lobby+0x8BE as a strict bool, E0A2 = +0x8BF, E0A3 = +0xE18.
#:    SETTLED DIRECTION: lobby+0x8BE != 0 MEANS CLEARANCE REVOKED. Every
#:    E0A1 test is `op19 r3(result), r2(#1); op29; op19 #0, r2; BRflag skip`
#:    (op19 = compare, 0x61110490 sets flag bit 0 on equal; op29 moves that
#:    bit into r2; 0x611103A0 sets bit 0 when r2 == 0; BRflag branches on
#:    bit 0), so the refusal lines run when E0A1 == 1:
#:      D63 0x1125A (Personnel Officer): D64 59-61 "Due to accumulated
#:        penalty points, your sortie, platoon formation and mission
#:        accept/issue clearance have been revoked."
#:      D63 0x10A60 (tag_mission), 0x1076A, 0x10DA6: D64 62 "mission
#:        clearance revoked"; D63 0xFD64, 0xFF76: D64 56 "sortie clearance";
#:        D63 0xFC32: D64 57 "platoon formation clearance".
#:      D83 0x2B22 (training ground entry): E0A1 == 1 calls the retraining
#:        branch 0x260E, else the normal one 0x2606.
#:    The one that looked contradictory: D83 0x284C, AFTER the retraining
#:    battle, compares E0A1 with 0 and prints D84 94/95 "Training completion
#:    was granted with the passage of time. Get back to regular duty!" on 0,
#:    i.e. once the server has CLEARED the byte; on 1 it shows D84 93
#:    "Retraining: current wins %d / wins to finish %d" = flag byte 175
#:    (E067 0xAF, read at 0x2764) and E0A3 = lobby+0xE18 (0x2752). Same
#:    direction. E0A2 (+0x419) has no reader in either script: served 0.
#: 4. RETRAINING. The training sortie E30A sends 0x0139 with create =
#:    (2 if lobby+0x8BE else 3) (0x610FC0E5 `neg dl; sbb; add 3`), and after
#:    it D83 0x2AD6 calls the server with event 211 (0xD3), p1 = 0: our cue
#:    to count the win (the server knows the verdict; the script sends none).
#: 5. IN BATTLE. 0x611F7475 reads unit+0x1C6 = POP body +0x1C2 (the body
#:    starts at unit+4): for a level L >= 2, unit+0xCB4 = min(100, 12(L-1))
#:    percent, which cuts ammo and BP (0x611F7600) and shows 32:13 / 32:15.
#:
#: OURS (SE published the mechanism, no numbers -- update 050628 104-108):
#: the threshold, the wins to finish, one point per YES, the penalty level =
#: the points held, and that ANY weapon counts (SE named missiles and
#: bazookas; the hit list's type byte is not decoded to a weapon class).
MSG_PENALTY_REPORT = 0x017B
MSG_PENALTY_GIVE = 0x017C
S17B_COUNT = 0x000
S17B_ROWS = 0x024
S17B_ROW_LEN = 0x38
S17B_MAX_ROWS = 10                      # 0x8C dwords = 560 B = 10 x 0x38
S17B_LEN = S17B_ROWS + S17B_ROW_LEN * S17B_MAX_ROWS
R17B_ID = 0x00
R17B_FIRST = 0x04
R17B_LAST = 0x15
R17B_NAME_LEN = 0x11                    # 16 chars + NUL, as the 0x014A names
S17C_LEN = 0x14                         # `push 0x14` at 0x611744A9
S17C_ID = 0x00

S14A_REVOKED = 0x03A                    # -> lobby+0x8BE (E0A1)
S14A_B8BF = 0x03B                       # -> lobby+0x8BF (E0A2, no reader)
S14A_RETRAIN_WINS = 0x594               # -> lobby+0xE18 (E0A3)
RETRAIN_FLAG = 175                      # E067 0xAF, D83 0x275A
EVENT_RETRAIN = 211                     # 0xD3, D83 0x2AD6
CREATE_RETRAIN = 2                      # 0x610FC0E5 with lobby+0x8BE set
CREATE_TRAINING = 3                     # ... and clear
POP_PENALTY_LEVEL = 0x1C2               # -> unit+0x1C6, read at 0x611F7475

#: FMO_PENALTY=1 (default): the whole loop. 0 = no 0x017B, the 0x017C is
#: logged and dropped, the three bytes go out as zeros (the old behaviour).
PENALTY = _env_int("FMO_PENALTY", "1") != 0
#: FMO_PENALTY_POINTS: points that revoke clearance. OURS.
PENALTY_POINTS = _env_int("FMO_PENALTY_POINTS", "3")
#: FMO_RETRAIN_WINS: retraining wins to get it back (+0x41A). OURS.
RETRAIN_WINS = _env_int("FMO_RETRAIN_WINS", "3")
#: FMO_PENALTY_BATTLE_CUT=1 (default): the self-POP carries the points as the
#: penalty level at body+0x1C2, so 2+ points cut ammo and BP in battle.
PENALTY_BATTLE_CUT = _env_int("FMO_PENALTY_BATTLE_CUT", "1") != 0


def _int(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def revoked(char):
    """True while `char`'s clearance is revoked."""
    return bool(PENALTY and char and _int(char.get("penalty_revoked")))


def penalty_bytes(char):
    """(+0x418, +0x419, +0x41A) for `char`: (1, 0, wins to finish) while
    revoked, else zeros. The SAME three bytes every 0x015A and the 0x014A carry:
    a 0x015A with zeros would silently hand a penalised pilot clearance back."""
    if not revoked(char):
        return 0, 0, 0
    return 1, 0, max(1, min(255, _int(char.get("retrain_wins_needed")) or RETRAIN_WINS))


def push_fields(char):
    """The b418/b419/b41a keywords of resultpush.result_push_body for `char`."""
    b = penalty_bytes(char or {})
    return {"b418": b[0], "b419": b[1], "b41a": b[2]}


def status_014a_fields(char):
    """The 0x014A fields (label, offset, bytes, source) for the penalty bytes;
    [] when there is no penalty (the client already holds zeros)."""
    b = penalty_bytes(char or {})
    if not b[0]:
        return []
    src = (f"character store: {_int(char.get('penalty_points'))} penalty point(s), "
           f"clearance revoked (FMO_PENALTY_POINTS={PENALTY_POINTS})")
    return [("penalty: clearance revoked (+0x3A -> lobby+0x8BE, E0A1)",
             S14A_REVOKED, bytes([b[0]]), src),
            ("penalty: retraining wins to finish (+0x594 -> lobby+0xE18, E0A3)",
             S14A_RETRAIN_WINS, bytes([b[2]]), src)]


def pop_level(char):
    """The penalty level the self-POP carries at body+0x1C2: the points
    held, 0..255. The client cuts nothing below 2 (0x611F747C)."""
    if not (PENALTY and PENALTY_BATTLE_CUT and char):
        return 0
    return max(0, min(255, _int(char.get("penalty_points"))))


def battle_cut_pct(level):
    """The client's own cut for `level` (0x611F7475): min(100, 12(L-1)) % for
    L >= 2, else 0. Pure; the log and the selftest use it."""
    return min(100, 12 * (level - 1)) if level >= 2 else 0


# --------------------------------------------------------------------------- #
# who is who
# --------------------------------------------------------------------------- #
def _sessions():
    return [s for s in list(trade.LIVE_SESSIONS.values()) if s is not None]


def session_for(key):
    """The live session whose account (or address) is `key` -- the chan_bkey
    a battle state is filed under -- or None."""
    for s in _sessions():
        if key and (getattr(s, "account", None) == key or getattr(s, "ip", None) == key):
            return s
    return None


def _playing(s):
    try:
        return s.playing_char() if charstore.CHAR_STORE else None
    except Exception:
        return None


def pop_extra_for(chan):
    """{POP_PENALTY_LEVEL: level} for a battle self-POP on `chan`, or {}."""
    if not (PENALTY and PENALTY_BATTLE_CUT):
        return {}
    s = session_for(referee.chan_bkey(chan)) or session_for(chan.addr[0])
    lv = pop_level(_playing(s) if s is not None else None)
    if lv < 2:
        return {}
    log(f"[udp {chan.addr[0]}:{chan.addr[1]}] PENALTY LEVEL {lv} at POP body+"
        f"{POP_PENALTY_LEVEL:#x} (-> unit+0x1C6): the client cuts ammo and BP by "
        f"{battle_cut_pct(lv)}% (0x611F7475) and says 32:13")
    return {POP_PENALTY_LEVEL: bytes([lv])}


# --------------------------------------------------------------------------- #
# 1. noting friendly fire (squad.squad_note_hits calls this for every cmd 43)
# --------------------------------------------------------------------------- #
def note_friendly_fire(chan, hits, arg8, now=None):
    """A pilot's own hit list (`hits` = parse_hitlist()['hits']) naming a
    pilot of its OWN side: filed on the victim's battle state as
    {shooter bkey: {...}}. Sides are the body+0x27 byte each self-POP
    carried (chan.pop_args['side']), the friend/foe the clients themselves
    were told. Returns [(victim bkey, shooter bkey)] noted."""
    if not PENALTY or arg8 is None or arg8 != chan.self_unit():
        return []
    my_side = (getattr(chan, "pop_args", None) or {}).get("side")
    if my_side is None:
        return []
    shooter = referee.chan_bkey(chan)
    out = []
    for t, _d, _a, _p in hits:
        vaddr = next((a for a, al in getattr(chan, "alias_of", {}).items() if al == t), None)
        vch = groupchannel.WORLD_PEERS.get(vaddr) if vaddr is not None else None
        if vch is None or not rooms._is_battle_chan(vch):
            continue
        if (getattr(vch, "pop_args", None) or {}).get("side") != my_side:
            continue
        victim = referee.chan_bkey(vch)
        if victim == shooter:
            continue
        ff = referee.battle_state(victim).setdefault("friendly_fire_by", {})
        row = ff.setdefault(shooter, {"account": getattr(chan, "account", None),
                                      "ip": chan.addr[0], "hits": 0,
                                      "at": now or time.time()})
        row["hits"] += 1
        out.append((victim, shooter))
    return out


# --------------------------------------------------------------------------- #
# 2. the report push (before the victim's 0x014C) and the vote (0x017C)
# --------------------------------------------------------------------------- #
def _name17(s):
    return (s or "").encode("ascii", "replace")[:R17B_NAME_LEN - 1] + b"\0"


def report_body(rows):
    """The 0x017B body for [(wire id, first, last)]: all 10 row slots are
    sent, since the arm copies 0x8C dwords whatever the count."""
    rows = list(rows)[:S17B_MAX_ROWS]
    b = bytearray(S17B_LEN)
    struct.pack_into("<I", b, S17B_COUNT, len(rows))
    for i, (wid, first, last) in enumerate(rows):
        o = S17B_ROWS + i * S17B_ROW_LEN
        struct.pack_into("<I", b, o + R17B_ID, int(wid) & 0xFFFFFFFF)
        f, la = _name17(first), _name17(last)
        b[o + R17B_FIRST:o + R17B_FIRST + len(f)] = f
        b[o + R17B_LAST:o + R17B_LAST + len(la)] = la
    return bytes(b)


def report_push(sess, conn_id):
    """The 0x017B for `sess`'s battle, or None: one row per pilot of its own
    side that hit it, each remembered on the session (penalty_offered) so a
    0x017C can only name a pilot we listed, once. Call it BEFORE the 0x014C."""
    if not PENALTY:
        return None
    st = referee.BATTLE_STATE.get(sess.battle_key()) or {}
    ff = st.get("friendly_fire_by") or {}
    if not ff or st.get("penalty_reported"):
        return None
    st["penalty_reported"] = True
    offered, rows = {}, []
    for shooter, row in ff.items():
        acct = row.get("account") or shooter
        os_ = session_for(shooter) or session_for(row.get("ip"))
        char = _playing(os_) if os_ is not None else None
        if char is None or not char.get("id"):
            log(f"{sess.peer}   penalty: friendly fire by {shooter} ({row['hits']} hit(s)) "
                f"NOT offered: no pilot on file for that player")
            continue
        wid = charlist.to_wire(char["id"])
        offered[wid] = {"account": getattr(os_, "account", None) or acct,
                        "char_id": char["id"],
                        "name": f"{char.get('first') or ''}.{char.get('last') or ''}"}
        rows.append((wid, char.get("first") or "", char.get("last") or ""))
    rows = rows[:S17B_MAX_ROWS]
    if not rows:
        return None
    sess.penalty_offered = {w: offered[w] for w, _f, _l in rows}
    log(f"{sess.peer}   -> 0x{MSG_PENALTY_REPORT:04X} FRIENDLY FIRE REPORT ({S17B_LEN}B, "
        f"BATTLE gate, before the 0x014C): "
        + ", ".join(f"{offered[w]['name']} (id {w:#x})" for w, _f, _l in rows)
        + " -- the client asks 11:9 per row after the battle; YES sends 0x017C")
    return pushes.lobby_push_packet(MSG_PENALTY_REPORT, report_body(rows), conn_id)


def add_point(char, now=None):
    """One penalty point on `char` (MUTATES it). At FMO_PENALTY_POINTS the
    clearance is revoked, the wins to finish set and the retraining counter
    (flag byte 175) zeroed. Returns [what changed]."""
    was = _int(char.get("penalty_points"))
    char["penalty_points"] = was + 1
    what = [f"penalty points {was} -> {was + 1}"]
    if not _int(char.get("penalty_revoked")) and was + 1 >= max(1, PENALTY_POINTS):
        char["penalty_revoked"] = 1
        char["retrain_wins_needed"] = max(1, min(255, RETRAIN_WINS))
        char["penalty_since"] = int(now or time.time())
        set_retrain_wins(char, 0)
        what.append(f"CLEARANCE REVOKED at {was + 1} point(s) (FMO_PENALTY_POINTS="
                    f"{PENALTY_POINTS}); {char['retrain_wins_needed']} retraining win(s) "
                    f"to finish (FMO_RETRAIN_WINS)")
    return what


def _flags(char):
    fl = fmostore.flags_bytes(char.get("flags")) if fmostore else b""
    if not fl:
        fl = status.reply_014a(char=char)[status.S14A_FLAGS11:status.S14A_FLAGS11 + status.S14A_FLAGS11_LEN]
    return bytearray(fl)


def retrain_wins(char):
    """Flag byte 175: the retraining wins so far (D84 93)."""
    fl = _flags(char)
    return fl[RETRAIN_FLAG] if len(fl) > RETRAIN_FLAG else 0


def set_retrain_wins(char, n):
    fl = _flags(char)
    if len(fl) > RETRAIN_FLAG:
        fl[RETRAIN_FLAG] = max(0, min(255, int(n)))
        char["flags"] = bytes(fl).hex()


def give_point(account, char_id, why):
    """Add a point to a stored pilot. ONLINE: queued on that session and
    applied on its own thread at its next keepalive (gatetool.gate_ops_due ->
    apply_due), because the session writes its in-memory roster back on every
    commit and would overwrite a store write from here. OFFLINE: the store
    directly; the next 0x014A serves it. Returns a log line."""
    s = next((x for x in _sessions() if getattr(x, "account", None) == account), None)
    if s is not None:
        q = getattr(s, "penalty_due", None)
        if q is None:
            q = s.penalty_due = []
        q.append((char_id, why))
        return f"queued on {account}'s live session (applied at its next keepalive)"
    if not charstore.CHAR_STORE:
        return "NOT kept: no character store"
    roster = charstore.load_roster(account)
    char = next((c for c in roster if c.get("id") == char_id), None)
    if char is None:
        return f"NOT kept: {account} has no pilot {char_id}"
    what = add_point(char)
    charstore.save_roster(account, roster)
    return f"{account} pilot {char_id} (offline): {'; '.join(what)}"


def apply_due(sess):
    """Apply the points queued on `sess` (its own thread). True when the
    playing pilot's bytes changed and want a 0x015A."""
    due = getattr(sess, "penalty_due", None)
    if not due:
        return False
    sess.penalty_due = []
    push = False
    playing = _playing(sess)
    for cid, why in due:
        char = next((c for c in (sess.roster or []) if c.get("id") == cid), None)
        if char is None:
            log(f"{sess.peer}   penalty: pilot {cid} is gone from this roster; dropped ({why})")
            continue
        before = penalty_bytes(char)
        what = add_point(char)
        try:
            sess.commit(f"penalty ({why}): {'; '.join(what)}")
        except Exception as e:
            log(f"{sess.peer}   WARNING: penalty NOT persisted ({e!r})")
        push = push or (playing is not None and playing.get("id") == cid
                        and penalty_bytes(char) != before)
    return push


def on_give(sess, p):
    """0x017C: the victim said YES for the id at +0x00. Fire-and-forget on the
    client (0x61174480 reads no reply), so this returns []."""
    b = p["payload"]
    wid = struct.unpack_from("<I", b, S17C_ID)[0] if len(b) >= 4 else None
    offered = getattr(sess, "penalty_offered", None) or {}
    if not PENALTY:
        log(f"{sess.peer}   0x{MSG_PENALTY_GIVE:04X} penalty vote for {wid!r} "
            f"dropped: FMO_PENALTY=0")
        return []
    row = offered.pop(wid, None) if wid is not None else None
    if row is None:
        log(f"{sess.peer}   0x{MSG_PENALTY_GIVE:04X} penalty vote for "
            f"{wid if wid is None else hex(wid)} IGNORED: not a pilot this "
            f"session's last 0x017B listed (or already counted -- one point per "
            f"victim per battle)")
        return []
    line = give_point(row["account"], row["char_id"],
                      f"friendly fire reported by {sess.peer}")
    log(f"{sess.peer}   0x{MSG_PENALTY_GIVE:04X} PENALTY POINT for {row['name']} "
        f"(id {wid:#x}): {line}")
    return []


# --------------------------------------------------------------------------- #
# 3. the sortie hook and the retraining event (211)
# --------------------------------------------------------------------------- #
def clearance_refusal(char, what):
    """(code, why) when a penalised pilot asks to join or form a platoon or
    to accept a mission (D64 59-61: 「小隊の結成・参加」「ミッションの受理」 are
    revoked with sortie clearance), else None. The scripts refuse first; this
    is the server's copy, with the generic code like sortie_verdict."""
    if not revoked(char):
        return None
    return (charselect.FAIL_CODE,
            f"PENALTY: {_int(char.get('penalty_points'))} penalty point(s), {what} "
            f"clearance revoked (D64 59-61) until the retraining is done")


def sortie_verdict(sess, q):
    """(code, why) when a penalised pilot asks for a battle sortie, else None.
    The training sorties (create 2 = retraining, 3 = training, 0x610FC0E5)
    stay open. Also notes whether THIS sortie is a retraining battle, which is
    what event 211 counts. The scripts refuse first (D64 56/57/62); this is
    the server's copy of that rule, with the generic 10:6 (no clearance code
    exists in the client's table 0x613955F0)."""
    create = q.get("create")
    sess.retrain_sortie = create == CREATE_RETRAIN
    char = _playing(sess)
    if not revoked(char) or create in (CREATE_RETRAIN, CREATE_TRAINING):
        return None
    return (charselect.FAIL_CODE,
            f"PENALTY: {_int(char.get('penalty_points'))} penalty point(s), sortie "
            f"clearance revoked (D64 56) -- only the retraining sortie "
            f"(create {CREATE_RETRAIN}) is open until "
            f"{_int(char.get('retrain_wins_needed'))} retraining win(s)")


def retrain_event(char, won):
    """Count one retraining battle for `char` (MUTATES it). Returns [what
    changed]; [] when nothing counts."""
    if not revoked(char) or not won:
        return []
    need = _int(char.get("retrain_wins_needed")) or RETRAIN_WINS
    wins = retrain_wins(char) + 1
    if wins >= need:
        pts = _int(char.get("penalty_points"))
        char["penalty_points"] = 0
        char["penalty_revoked"] = 0
        char["retrain_wins_needed"] = 0
        set_retrain_wins(char, 0)
        return [f"retraining win {wins}/{need}: CLEARANCE RESTORED, penalty points "
                f"{pts} -> 0, flag byte {RETRAIN_FLAG} -> 0"]
    set_retrain_wins(char, wins)
    return [f"retraining win {wins}/{need} (flag byte {RETRAIN_FLAG} = {wins})"]


def on_script_event(sess, payload, char, ev, ps, conn_id):
    """Event 211 (the retraining battle is over): count a WON retraining sortie
    once, and when that changes the pilot push the answered record with the
    new flags and bytes in a 0x015A BEFORE the ack -- D83 0x284C reads E0A1
    straight after the call. Returns the packets ([] = plain ack)."""
    if not PENALTY or ev != EVENT_RETRAIN or char is None:
        return []
    st = getattr(sess, "battle_settlement", None) or {}
    if not getattr(sess, "retrain_sortie", False):
        log(f"{sess.peer}   penalty: event {EVENT_RETRAIN} but the last sortie was not a "
            f"retraining one (create {CREATE_RETRAIN}) -- nothing counted")
        return []
    if st.get("retrain_counted"):
        return []
    st["retrain_counted"] = True
    what = retrain_event(char, st.get("won"))
    if not what:
        log(f"{sess.peer}   penalty: retraining battle "
            f"{'WON' if st.get('won') else 'not won'}; "
            f"{'no penalty on this pilot' if not revoked(char) else 'no win counted'}")
        return []
    for w in what:
        log(f"{sess.peer}   penalty: {w}")
    try:
        sess.commit("; ".join(what))
    except Exception as e:
        log(f"{sess.peer}   WARNING: retraining NOT persisted ({e!r})")
    owned = bytearray(status.reply_014a(char=char)[
        status.S14A_OWNED:status.S14A_OWNED + resultpush.S15A_OWNED_LEN])
    o = status.S14A_FLAGS11 - status.S14A_OWNED
    owned[o:o + status.S14A_FLAGS11_LEN] = bytes(_flags(char)[:status.S14A_FLAGS11_LEN])
    rec = (scriptcall.answered_0159(payload, ps)
           if len(payload) == scriptcall.S159_BODY_LEN else b"")
    return [resultpush.result_push_packet(conn_id, record=rec, money=0, contribution=0,
                                          owned=bytes(owned), pilot=char)]


# Called at run time only; imported last so that import cycles resolve.
from .deps import fmostore  # noqa: E402
from . import (  # noqa: E402
    charlist, charselect, charstore, groupchannel, pushes, referee, resultpush, rooms,
    scriptcall, status, trade,
)
