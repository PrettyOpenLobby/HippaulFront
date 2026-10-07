"""The script's server call (0x0159): event rules from fmo-events.tsv and the answered record."""
import os
import re
import struct
from .deps import fmostore
from .knobs import _env_int


#: KEY: `0x0159` -- THE MESSAGE THAT HANGS THE SCRIPT-98 CUTSCENE.
#: Decoded 2026-09-04 from a LIVE hang: with FMO_STATUS_NATION=1 the client
#: registers scene script 98 (script_id_for), that script plays an opening NPC
#: scene, and at the end of it the client sends a 1,432-byte `0x0159` and waits.
#: We had no handler; the log said `no handler for msg=0x0159` and the session
#: sat in keepalives forever.
#:
#: THE CONTRACT, read out of the sender's own state machine (`0x611779E0`,
#: state at lobby+0x6E4A, jump table 0x61177B18, four states):
#:   state 1 (0x61177A00): builds id 0x159 with a 0x598 = 1,432-byte body copied
#:       (`rep movsd 0x166`) from lobby+0x6E4E, sends it, stores the POLL
#:       SEQUENCE at lobby+0x73E6, and advances to state 2.
#:   state 2 (0x61177A72): polls that sequence with 0x61199E30. `jle` -> stay in
#:       state 2, which is the hang. On a reply it runs 0x61175250 (which only
#:       decrements m_ActiveSessionCount -- "ERROR:m_ActiveSessionCount < 0" --
#:       and does NOT read our body) and then:
#:           mov eax, 1 ; cmp word [esi+6], ax ; jne <fail>
#:       i.e. the reply's MESSAGE ID must be **1**. On a match it copies its own
#:       1,432 bytes out to the caller and sets lobby+0x73EA = 1; on any other
#:       id it sets lobby+0x73EA = -1. Both advance to state 3, so a WRONG id is
#:       a graceful failure and only SILENCE is a hang.
#:
#: KEY: So the payload is irrelevant -- nothing in that path reads it -- and the
#: entire fix is an empty message-1 on the client's own sequence. This is the
#: same "message 1" acknowledgement convention MSG_SESSION_START already uses.
#:
#: WARNING: WHAT WE ARE ACKNOWLEDGING. The client hands us 1,432 bytes and takes a
#: 1 as "accepted". We do not decode that block, so this IS an acknowledgement
#: of something we did not store -- a habit flagged before. It is
#: taken deliberately, because the alternative measured on 2026-09-04 is an
#: infinite wait, and because the client's own failure arm is graceful: set
#: FMO_ANSWER_0159=fail to send id 2 instead and let the script take its -1
#: path, or 0 to reproduce the hang.
MSG_0159_REQ = 0x0159
S159_BODY_LEN = 0x598                  # 1432, the `rep movsd 0x166` at 0x61177A45

# --------------------------------------------------------------------------- #
# KEY: 0x0159 IS THE SCRIPT'S SERVER CALL (static 2026-09-11, the DLL and the script bytecode)
# --------------------------------------------------------------------------- #
#: The "cutscene terminator" reading above is one CALLER of a general
#: mechanism. Script syscall **0xE220** (`0x610FDD10`) takes a struct
#: {u32 EVENT id @+0x00, u32 params[16] @+0x04..} from the script, clears the
#: VM's 1,432-byte record at vm+0xF02 (`0x610F9E80`), writes the 16 params at
#: record+0x458..+0x497 and the event id at +0x00 (`0x610F9CB0`), and submits
#: it as this message (`0x611778F0` -> lobby+0x6E4E -> the machine at
#: lobby+0x6E4A). Syscall **0xE222** (`0x610FDD60` -> `0x610F9F30`) is the
#: poll+read: it returns the machine's code (1 = done, 2 = failed, 3) and
#: hands the script back the 16 params FROM THE RECORD -- the record as it
#: stands after the reply, which the ack (message 1) copies out of
#: lobby+0x6E4E (`0x61177AB5`). The ONLY server->client writer of
#: lobby+0x6E4E is the 0x015A arm's `+0xAD8 == 0` copy (`0x6117EA49`).
#:
#: So SE's server ANSWERED these calls: it processed the event, pushed the
#: processed record back in 0x015A (params rewritten, owned table + flags
#: refreshed, item/money deltas applied) and then acked with message 1, and
#: the script branched on what came back. Every script ships the same helper
#: library (`AI/F00/D87.DAT` 0x740..: `srv_101()`, `srv_200(p)`, `srv_201(p)`,
#: `srv_204(p)`, `srv_104(p) -> p1`, ...); the LIVE captures (logs/captures/
#: fmo-0159-*.bin, 09-05..09-11) are {101, [] } at lobby entry, {104, [130]}
#: for Kwangsu Son, {200, [16, 104, 130]} for the Map Selector, {205, [34,
#: 104, 130]} for the sergeant, {102, [0, row, col, ...]} for a sector -- and
#: 130 is `own_byte` of "Enemy Unit Annihilation", Son's own mission in
#: fmodata/fmo-missions.tsv. The params NAME the mission's progress byte.
#:
#: What we do with it (2026-09-11): DECODE every call, name the mission its
#: params point at, PERSIST the call on the pilot (`srv_events` in the
#: record's extra JSON: last params + count per event id), and apply
#: fmodata/fmo-events.tsv -- SE's server-side rule for an event is not in
#: the client, so the rules are DATA: a row may rewrite the answer params
#: and/or write flag bytes; when a row changes anything we push the answered
#: record in 0x015A (+0xAD8 = 0) and THEN ack, exactly the order the client's
#: machine needs. With no matching row the behaviour is today's: message 1,
#: the script reads its own params back. The table ships with the candidate
#: rows COMMENTED OUT -- each is inferred, and enabling one is a live test.
S159_EVENT = 0x000                    # u32 event id (script arg to 0xE220)
S159_PARAMS = 0x458                   # u32[16] params (0x610F9E80 writes +0x458..)
S159_NPARAMS = 16
S159_B418 = 0x418                     # the three bytes result_push_body authors
EVENTS_TSV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "fmodata", "fmo-events.tsv")


def parse_0159(payload):
    """(event id, [16 params]) from a 0x0159 body; (None, []) when short."""
    if len(payload) < S159_PARAMS + 4 * S159_NPARAMS:
        return None, []
    ev = struct.unpack_from("<I", payload, S159_EVENT)[0]
    ps = list(struct.unpack_from("<%dI" % S159_NPARAMS, payload, S159_PARAMS))
    return ev, ps


def mission_for_byte(idx):
    """'O.C.U. "Enemy Unit Annihilation" (Lv 6)' for a flag byte that is some
    mission's own progress byte, else None. Names what a script call is about."""
    for nation, rows in (progress.MISSIONS or {}).items():
        for m in rows:
            if m.get("own") == idx:
                return f"{nation} \"{m.get('title')}\" (Lv {m.get('level')})"
    return None


def describe_0159(ev, ps):
    """One line: the event, its non-zero params, and the mission any param
    names -- the log line the next decode session reads first."""
    nz = [(i + 1, v) for i, v in enumerate(ps) if v]
    bits = [f"p{i}={v}" + (f" [flag byte {v} = {mission_for_byte(v)}]"
                           if mission_for_byte(v) else "")
            for i, v in nz]
    return (f"script server call EVENT {ev} ({ev:#x}) with "
            + (", ".join(bits) if bits else "no params"))


def load_event_table(path=EVENTS_TSV):
    """[{event, p1, answer:{n: expr}, set:{idx: val}, note}] from the TSV.

    Columns: event  p1  answer  set  note. `p1` is a literal or `*`. `answer`
    is `pN=<expr>[;...]` where <expr> is an int, `pM` (another param) or
    `flag[pM]` / `flag[<idx>]` (the pilot's flag byte). `set` is the
    FMO_STATUS_FLAGS grammar (`128=99`, `173`) with `pM` allowed as an index
    (`p1=99` = set the byte the script named to 99). Lines starting with #
    are comments -- the shipped candidates live there until proven."""
    rows = []
    try:
        with open(path, encoding="utf-8") as fh:
            lines = [ln.rstrip("\r\n") for ln in fh]
    except OSError:
        return rows
    for ln in lines:
        if not ln.strip() or ln.lstrip().startswith("#") or ln.startswith("event\t"):
            continue
        cols = ln.split("\t")
        while len(cols) < 5:
            cols.append("")
        try:
            ev = int(cols[0], 0)
        except ValueError:
            continue
        p1 = cols[1].strip()
        rows.append({"event": ev, "p1": None if p1 in ("", "*") else int(p1, 0),
                     "answer": cols[2].strip(), "set": cols[3].strip(),
                     "note": cols[4].strip()})
    return rows


EVENT_RULES = load_event_table()


def event_rule_for(ev, ps, rules=None):
    """The first rule matching (event, p1): a literal p1 beats `*`."""
    rules = EVENT_RULES if rules is None else rules
    p1 = ps[0] if ps else 0
    exact = [r for r in rules if r["event"] == ev and r["p1"] == p1]
    star = [r for r in rules if r["event"] == ev and r["p1"] is None]
    return (exact or star or [None])[0]


def _event_term(term, ps, flags):
    """Evaluate one rule term: int, pM, flag[pM], flag[idx]."""
    t = term.strip()
    m = re.fullmatch(r"flag\[(p(\d+)|\d+)\]", t)
    if m:
        idx = int(ps[int(m.group(2)) - 1]) if m.group(2) else int(m.group(1))
        return flags[idx] if 0 <= idx < len(flags) else 0
    m = re.fullmatch(r"p(\d+)", t)
    if m:
        return int(ps[int(m.group(1)) - 1])
    return int(t, 0)


#: KEY: THE MISSION STEPS (2026-09-30, static; code offsets of fmogates' SCP
#: decoder). What the scripts prove about 104 and 105:
#:   * srv_104(byte) (D87 0x6e4, D89 0x6ee) and srv_105(byte) (D89 0x122c)
#:     return the answered p1, and NO caller reads it: every CALL of a 104/105
#:     wrapper in the 48 scripts that have one is followed by an instruction
#:     that overwrites R0 or ignores it (the LobbySally at D87 0xd822 loads its
#:     own var 0x460 = 2 over it and debug-prints that as "<-event status").
#:   * srv_105 itself reads answered p2 (var 0x364, debug-printed at D89
#:     0x129a as "reward flag"); p2 != 0 runs the mission's reward lines
#:     (0x824(byte): D90 messages 0xd9/0xda "Obtained the armor color ...",
#:     0xdb "Earned 300H$") and, if the contribution (E06F) rose across the
#:     call, 0xd8 "Contribution points earned." The old "p2 compared to
#:     0x169321 before 0xD800" was inline debug text read as code.
#:   * WHEN they are called: 104 on a mission byte == 0 after the offer scene
#:     (D89 0x3a48 for byte 130, 0x53e2 for 137) and from LobbySally on byte
#:     == 1 with the sortie on one of the mission's tiles; 105 on byte == 3
#:     after the clear scene (D89 0x3b9e, 0x53f4).
#: So the ANSWER barely matters and the SIDE EFFECT is the point. INFERRED
#: (progress.py has the full reading): `@step` = 104: 0 -> 1 (accepted),
#: 1 -> 2 (sortied on it); `@report` = 105: 3 -> 99 (reported). Both only on
#: a catalogue mission's own byte (never byte 128, which the registration
#: scripts also pass to 104/105), both answer p1 = the byte's value after the
#: step, and `@report` answers p2 = 1 when the byte has a reward (see
#: MISSION_REWARD), else 0. FMO_MISSION_SCRIPT_STEPS=0 turns both actions into
#: no-ops.
#:
#: KEY: BYTE 128 IS A MISSION TOO (2026-10-01, static, SCP 0x8074 + 0x8084).
#: The First Sergeant switches on byte 128 == 0 / 1 / 2 / 99 only (0x4ffe..
#: 0x5016): 0 = greeting, then 201 [job] and 104 [128] (the training sortie);
#: 1 = "that training run was a failure... one more chance"; 2 = "You're
#: back... cleared to sortie", the base insignia, then 105 [128]; 99 = "go talk
#: to the operator". The training-success scene 0x8084 (kind 4, key 0x2710)
#: calls 104 [128] once more. So 104 steps 128 like any mission (0 -> 1 -> 2)
#: and 105 reports it from 2, not 3. Until 2026-10-01 fmo-events.tsv wrote the
#: 201 job number into byte 128 and nothing ever set 99, so no new pilot could
#: register (FMO_TRAINING_GATE refuses sorties below 99).
MISSION_SCRIPT_STEPS = _env_int("FMO_MISSION_SCRIPT_STEPS", 1) != 0
REGISTRATION_BYTE = 128
REGISTRATION_CLEARED = 2
#: KEY: MISSION REWARDS (2026-10-01): srv_105 shows the byte's reward lines when
#: the answered p2 != 0 (the library's 0x824 switch; fmo-gates.json `rewards`,
#: decoded by fmogates.mission_rewards). The lines only SHOW; the server pays
#: the money (session: the 0x015A answer's money delta + the stored wallet) and
#: mints the OC transit pass of bytes 139/140. Armor colours, camo and insignia
#: need no grant: every cosmetic is already in the open catalogue (cosmetics.py).
#: FMO_MISSION_REWARD=0 answers p2 = 0 and pays nothing, the pre-10-01 behaviour.
MISSION_REWARD = _env_int("FMO_MISSION_REWARD", 1) != 0


def _mission_action(name, ps, flags, out, what):
    """Apply one `@action` of a rule: MUTATES flags / out / what."""
    idx = int(ps[0]) if ps else -1
    reg = idx == REGISTRATION_BYTE
    if not MISSION_SCRIPT_STEPS or not (reg or idx in progress.story_bytes()):
        return
    was = flags[idx]
    to = was
    if name == "step" and was in (0, 1):
        to = was + 1
    elif name == "report" and was == (REGISTRATION_CLEARED if reg else progress.CLEARED):
        to = progress.DONE
    elif name not in ("step", "report"):
        raise ValueError("unknown fmo-events.tsv action @%s" % name)
    if to != was:
        flags[idx] = to
        what.append(f"flag byte {idx} {was} -> {to} (@{name})")
    if out[0] != to:
        what.append(f"answer p1 {out[0]} -> {to}")
        out[0] = to
    if name == "report" and len(out) > 1:
        pay = 1 if (MISSION_REWARD and to != was and not reg
                    and progress.mission_reward(idx)) else 0
        if out[1] != pay:
            what.append(f"answer p2 {out[1]} -> {pay}"
                        + (" (the reward lines play)" if pay else ""))
            out[1] = pay


def apply_event_rule(rule, ps, flags):
    """(answer params, new flags bytes, [what changed]) for one rule. Pure.

    A `set` term `@step` / `@report` is a mission step (see
    MISSION_SCRIPT_STEPS); it runs FIRST, so an `answer` term in the same row
    reads the flags after the step."""
    flags = bytearray(flags if flags else bytes(fmostore.FLAGS_LEN if fmostore else 256))
    out = list(ps)
    what = []
    sets = [s.strip() for s in (rule.get("set") or "").split(";") if s.strip()]
    for s in sets:
        if s.startswith("@"):
            _mission_action(s[1:], ps, flags, out, what)
    for a in filter(None, (rule.get("answer") or "").split(";")):
        lhs, _, rhs = a.partition("=")
        n = int(lhs.strip()[1:])
        v = _event_term(rhs, ps, flags) & 0xFFFFFFFF
        if out[n - 1] != v:
            what.append(f"answer p{n} {out[n - 1]} -> {v}")
        out[n - 1] = v
    for s in sets:
        if s.startswith("@"):
            continue
        if "=" in s:
            lhs, _, rhs = s.partition("=")
            idx = _event_term(lhs, ps, flags)
            val = _event_term(rhs, ps, flags) & 0xFF
            if 0 <= idx < len(flags) and flags[idx] != val:
                what.append(f"flag byte {idx} {flags[idx]} -> {val}")
                flags[idx] = val
        else:
            fid = _event_term(s, ps, flags)
            if 0 <= fid < len(flags) * 8 and not flags[fid >> 3] & (1 << (fid & 7)):
                what.append(f"flag bit {fid} set")
                flags[fid >> 3] |= 1 << (fid & 7)
    return out, bytes(flags), what


def flags_push_packet(sess, conn_id, char):
    """A 0x015A that only refreshes the owned table with `char`'s flag block:
    no record (+0xAD8 = 1), no money, and the three script bytes +0x418..
    = `char`'s CURRENT penalty bytes (penalty.penalty_bytes; they used to echo
    the last 0x0159's +0x418.., a script record, not the pilot's state) -- the
    push gatetool.gate_ops_due sends for a story-gates edit, a penalty change,
    and a server-side flag change outside a script call (a mission accepted
    on the board)."""
    owned = bytearray(status.reply_014a(char=char)[
        status.S14A_OWNED:status.S14A_OWNED + resultpush.S15A_OWNED_LEN])
    fl = fmostore.flags_bytes(char.get("flags")).ljust(status.S14A_FLAGS11_LEN, b"\0")
    o = status.S14A_FLAGS11 - status.S14A_OWNED
    owned[o:o + status.S14A_FLAGS11_LEN] = fl[:status.S14A_FLAGS11_LEN]
    return resultpush.result_push_packet(conn_id, record=b"", money=0, contribution=0,
                                         owned=bytes(owned), pilot=char)


def answered_0159(payload, params):
    """The client's own record with the 16 params rewritten -- the 0x015A
    record that hands the script the server's answer."""
    b = bytearray(payload)
    struct.pack_into("<%dI" % S159_NPARAMS, b, S159_PARAMS,
                     *[int(v) & 0xFFFFFFFF for v in params])
    return bytes(b)
#: '1' (default) = reply message 1, the id state 2 compares against and the only
#: one it reads as success. 'fail' = reply id 2, the graceful -1 arm. '0' =
#: stay silent, which is what hung the client on 2026-09-04.
ANSWER_0159 = os.environ.get("FMO_ANSWER_0159", "1").strip() or "1"
#: KEY: FMO_0159_REGRANT=1 -- after acknowledging the tutorial's 0x0159 commit,
#: ALSO push a fresh 0x0153 area grant (a synthesized 0x0150 through the same
#: handler, so the grant is byte-identical to a login's, on the push sequence
#: 0x7FFFFFFE). Why (live 2026-09-05): the tutorial script ends with E200 phase
#: 0, the client drops to scene 6, and scene 6 has NO map object and therefore
#: NO script contexts (census + shim: after the settle not one script command is
#: dispatched, and the client attaches no native action to an NPC target). Yet
#: the tutorial's last line tells the player to "talk to the two leaders" --
#: so SE's settled lobby had talkable NPCs, which needs a script, which needs
#: a map -- i.e. the client must be re-entered into the lobby after the
#: tutorial, and the only thing that does that is a 0x0153 (the Change Area
#: arm 0x611796EF tears the world down and re-enters on EVERY accepted 0x0153).
#: This is the server-side hypothesis test. ONE-SHOT per session: if the
#: re-entered script plays the tutorial again, the second 0x0159 gets only the
#: ack, so it cannot loop. Default off.
REGRANT_0159 = os.environ.get("FMO_0159_REGRANT", "").strip() not in ("", "0")


# Called at run time only; imported last so that import cycles resolve.
from . import progress, resultpush, status  # noqa: E402
