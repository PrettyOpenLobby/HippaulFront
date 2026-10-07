"""The training ground's result and the Drill Instructor's "Training result ranking"."""
import struct
import time
from .knobs import _env_int


# --------------------------------------------------------------------------- #
# THE TRAINING RESULT RANKING (S:77:1 "訓練結果ランキング")
# --------------------------------------------------------------------------- #
#: VERIFIED: STATIC 2026-10-07. THE RANKING IS KEPT BY THE CLIENT, NOT BY US.
#: There is no ranking request and no ranking reply. The whole table lives in
#: one 0xE4-byte heap object behind global 0x613B9028 (allocated 0x610D1C00,
#: zeroed by 0x610D1480, freed at shutdown 0x610D1C70), and it is saved and
#: loaded as LOCAL SLOT 9 of the user config table 0x6138FE88:
#: "/usr/config/trainingscore%08x.cfg" (writer 0x61080BE0, reader 0x61080CD0,
#: both `push 0xE4; push [0x613B9028]; push 9`). Its layout:
#:   +0x08  the LAST result ("今回の訓練結果", S:77:0), one row
#:   +0x1C  the TOP TEN, 10 rows of 0x14, best first
#:   row:   +0x00 s32 score   +0x04 s32 score per minute   +0x08 s32 kills
#:          +0x0C s32 PCs on the pilot's side   +0x10 u32 the client's clock
#: The window (class vtable 0x61333500, draw 0x610D1830) prints "Score"
#: "%d ( %d/min )  %3d Kills  PC : %2d" for the last result and the ten rows
#: under S:77:1, with the date from the row clock or "00/00/00 00:00".
#:
#: KEY: WHAT FEEDS IT IS THE BATTLE END. The 0x014C arm (0x6117E33B) calls
#: 0x610D1C30(payload+0x2CC tail, payload+0x104 block) when the block's
#: +0x0C (payload +0x110, battleend.S14C_REPLAY) == 1, and 0x610D1580 builds
#: the row from the server's numbers:
#:   score   = (int) float at tail+0x14 = payload+0x2E0
#:   per min = (int) float at tail+0x18 = payload+0x2E4
#:   kills   = byte block[0x6C + (2 if nation == 1 else 1)] = payload+0x170+n
#:             (the OTHER side's slot; script natives 0x610FA460 read the
#:             same lobby+0x6A32 bytes)
#:   PCs     = byte block[0x64 + nation] = payload+0x168+n (natives
#:             0x610FA430, lobby+0x6A2A)
#:   nation  = 0x61016F00 = lobby+0x8B4 (zoneentry.script_nation)
#: and inserts it: walk the ten rows, a higher score goes in front, an equal
#: score goes in front unless its per-minute is LOWER; rows below shift down.
#: So "per training setting" does not exist on the client: one table per
#: character file, every training battle in it.
#:
#: KEY: WHEN THE WINDOW OPENS. Lobby state 8 (0x61173AA0) picks the post-battle
#: state from the mission block's +0x6C (lobby+0x5CEA): 1 -> 0xB, 6 -> 0xD (the
#: Coliseum, coliseum.ARENA_BATTLE_KIND), 7 -> 0xE, else 7. In state 0xB the
#: result machine 0x61176C40 sub-state 0 (0x61176CE0) creates this window
#: (0x610D1680) instead of the EXP Gain path. A script native (0x610FB1D0,
#: registered at 0x613B98C8; opcode not resolved) opens the same window from
#: the lobby, which is the Drill Instructor's "view the ranking" item.
#: Nothing else reads lobby+0x5CEA == 1 (the other readers compare 2 and 7).
#:
#: WHAT IS OURS (SE published no formula): the score itself. SE's server
#: computed it; the client only stores and sorts it. Ours: FMO_TRAINING_KILL_SCORE
#: per enemy destroyed plus FMO_TRAINING_WIN_SCORE for a win, per minute over
#: the time since the sortie grant (at least one minute).
TRAINING_FLAG = 0x110                 # payload; == battleend.S14C_REPLAY
TAIL_SCORE = 0x14                     # within the 72-B tail (payload +0x2E0)
TAIL_PER_MIN = 0x18                   # (payload +0x2E4)
BLOCK_TRAINING = 0x0C                 # within the 372-B block (payload +0x110)
BLOCK_PCS = 0x64                      # + nation
BLOCK_LOST = 0x6C                     # + the other nation
BLOCK_LEN = 0x5D * 4
TAIL_LEN = 0x12 * 4
#: The 0x013A mission block's battle kind (lobby+0x5CEA); 1 = training.
MB_BATTLE_KIND = 0x6C
TRAINING_BATTLE_KIND = 1
#: The client file.
FILE_LEN = 0xE4
FILE_LAST = 0x08
FILE_ROWS = 0x1C
ROW_LEN = 0x14
ROWS = 10
#: The training sorties: 0x610FC0E5 sends create 2 (retraining) or 3.
CREATE_KINDS = (2, 3)

#: FMO_TRAINING_RANKING: 1 (default) = a training sortie's 0x014C carries the
#: score (+0x110 = 1, the two floats, the kill and PC bytes), so the client
#: files it in its own ranking; 0 = the old zero fields.
RANKING = _env_int("FMO_TRAINING_RANKING", "1") != 0
#: FMO_TRAINING_KIND: 0 (default) = the 0x013A block keeps kind 0 and the
#: post-battle flow is the one every training run so far took. 1 = kind 1, so
#: the client opens the Training Result window after the battle (state 0xB).
#: WARNING: OFF ON PURPOSE. State 0xB changes the post-battle path of the
#: REGISTRATION training (byte 128), which works today. One-launch check: a
#: training sortie with this on shows "This training result" after the battle
#: and the sergeant still clears the pilot (byte 128 -> 2 -> 99).
KIND = _env_int("FMO_TRAINING_KIND", "0") != 0
KILL_SCORE = _env_int("FMO_TRAINING_KILL_SCORE", "100")
WIN_SCORE = _env_int("FMO_TRAINING_WIN_SCORE", "500")
#: How many results the server keeps per pilot (its own copy, for the board).
KEEP = ROWS


def is_training(create):
    return create in CREATE_KINDS


def score_for(kills, won, secs):
    """(score, per minute) for one training battle. OURS (see above)."""
    score = max(0, int(kills)) * KILL_SCORE + (WIN_SCORE if won else 0)
    mins = max(1.0, float(secs or 0) / 60.0)
    return score, score / mins


def end_fields(nation, kills, pcs, score, per_min):
    """{"block", "tail"} for battleend.battle_end_body: the ranking feed. The
    named fields battle_end_body writes over them (verdict, flags, platoon
    money...) do not touch the bytes set here."""
    block = bytearray(BLOCK_LEN)
    tail = bytearray(TAIL_LEN)
    struct.pack_into("<I", block, BLOCK_TRAINING, 1)
    n = int(nation) if nation in (1, 2) else 1
    block[BLOCK_PCS + n] = max(0, min(255, int(pcs)))
    block[BLOCK_LOST + (2 if n == 1 else 1)] = max(0, min(255, int(kills)))
    struct.pack_into("<f", tail, TAIL_SCORE, float(score))
    struct.pack_into("<f", tail, TAIL_PER_MIN, float(per_min))
    return {"block": bytes(block), "tail": bytes(tail)}


def client_row(body, nation):
    """(score, per_min, kills, pcs) as 0x610D1580 reads them out of a 0x014C
    payload for a pilot of `nation`, or None when +0x110 != 1 (the arm does
    not call it). The selftest's model of the client: ftol truncates."""
    if len(body) < 0x2E8 or struct.unpack_from("<I", body, TRAINING_FLAG)[0] != 1:
        return None
    blk, tl = 0x104, 0x2CC
    return (int(struct.unpack_from("<f", body, tl + TAIL_SCORE)[0]),
            int(struct.unpack_from("<f", body, tl + TAIL_PER_MIN)[0]),
            body[blk + BLOCK_LOST + (2 if nation == 1 else 1)],
            body[blk + BLOCK_PCS + nation])


def insert(rows, row):
    """0x610D1580's insert, on a list of (score, per_min, kills, pcs, when)
    rows best first. Returns (new rows, index or None when it did not place)."""
    rows = list(rows)[:ROWS]
    # the client's table always has ten rows; empty ones are zeros, which a
    # zero score still beats on per-minute >= 0 (that is how the first lands)
    full = rows + [(0, 0, 0, 0, 0)] * (ROWS - len(rows))
    for i in range(ROWS):
        s, pm = full[i][0], full[i][1]
        if row[0] > s or (row[0] == s and row[1] >= pm):
            out = full[:i] + [tuple(row)] + full[i:ROWS - 1]
            return [r for r in out if any(r)], i
    return rows, None


def file_body(last, rows):
    """The 0xE4 bytes of trainingscore%08x.cfg for `last` and `rows`."""
    b = bytearray(FILE_LEN)
    for at, r in [(FILE_LAST, last)] + [(FILE_ROWS + i * ROW_LEN, r)
                                       for i, r in enumerate(rows[:ROWS])]:
        if r:
            struct.pack_into("<iiiiI", b, at, *(int(x) for x in r))
    return bytes(b)


def leaderboard(chars, n=10):
    """[(score, per_min, name, char id)] best per pilot across `chars`, for a
    board or an admin page (the client has no server ranking to show)."""
    out = []
    for c in chars or ():
        rows = (c or {}).get("training_scores") or []
        if rows:
            best = rows[0]
            name = ".".join(x for x in (c.get("first"), c.get("last")) if x)
            out.append((int(best[0]), float(best[1]), name, c.get("id")))
    out.sort(key=lambda r: (-r[0], -r[1]))
    return out[:n]


def own_rank(chars, char_id):
    """1-based place of `char_id` in leaderboard(chars, all), or None."""
    for i, r in enumerate(leaderboard(chars, n=1 << 30)):
        if r[3] == char_id:
            return i + 1
    return None


# --------------------------------------------------------------------------- #
# the hooks
# --------------------------------------------------------------------------- #
def on_sortie_granted(sess, q, body, block_off):
    """Called with the 0x013A body just before it goes out: note whether this
    is a training sortie, and with FMO_TRAINING_KIND=1 mark its block kind 1.
    Returns the body to send. Never raises."""
    try:
        from . import referee
        # kept IN the battle state, which every sortie grant resets, so a
        # later arena or war sortie on this session can never inherit it
        on = is_training(q.get("create"))
        referee.battle_state(sess.battle_key())["training"] = on
        if not (on and KIND and body):
            return body
        b = bytearray(body)
        struct.pack_into("<I", b, block_off + MB_BATTLE_KIND, TRAINING_BATTLE_KIND)
        log(f"{sess.peer}   TRAINING: create {q.get('create')} -> block +0x6C = 1 "
            f"(lobby+0x5CEA; post-battle state 0xB opens the Training Result "
            f"window, FMO_TRAINING_KIND=1)")
        return bytes(b)
    except Exception as e:              # bookkeeping must never cost the sortie
        log(f"{getattr(sess, 'peer', '?')}   WARNING: training sortie note failed ({e!r})")
        return body


def battle_end_fields(sess, st):
    """The 0x014C kwargs (block/tail) for a training sortie's end, {} for any
    other battle. Also files the result on the pilot. Never raises."""
    if not RANKING:
        return {}
    try:
        from . import charstore, referee, zoneentry
        bst = referee.BATTLE_STATE.get(sess.battle_key()) or {}
        if not bst.get("training"):
            return {}
        char = sess.playing_char() if charstore.CHAR_STORE else None
        nation = zoneentry.script_nation(char)[0]
        t0 = bst.get("started_at") or bst.get("granted_at") or time.time()
        secs = max(0.0, time.time() - t0)
        kills = len(st.get("kills") or ())
        pcs = max(1, int(getattr(sess, "platoon_n", None) or 1))
        score, per_min = score_for(kills, st.get("won"), secs)
        f = end_fields(nation, kills, pcs, score, per_min)
        log(f"{sess.peer}   TRAINING RESULT: score {score} ({int(per_min)}/min over "
            f"{int(secs)} s), {kills} kill(s), PC {pcs}, nation {nation} -> 0x014C "
            f"+0x110 = 1, +0x2E0/+0x2E4 floats, block +0x{BLOCK_PCS:X}/+0x{BLOCK_LOST:X} "
            f"bytes; the client files it in trainingscore%08x.cfg (0x610D1580)")
        if char is not None:
            rows = [tuple(r) for r in (char.get("training_scores") or [])]
            rows, at = insert(rows, (score, round(per_min, 2), kills, pcs, int(time.time())))
            char["training_scores"] = [list(r) for r in rows[:KEEP]]
            if at is not None:
                try:
                    sess.commit(f"training result {score} filed at place {at + 1}")
                except Exception as e:
                    log(f"{sess.peer}   WARNING: training result NOT banked ({e!r})")
        return f
    except Exception as e:              # a ranking must never cost the battle end
        log(f"{getattr(sess, 'peer', '?')}   WARNING: training result not built ({e!r})")
        return {}


from .wirelog import log  # noqa: E402
