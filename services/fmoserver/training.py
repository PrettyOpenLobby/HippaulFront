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
#: FMO_TRAINING_KIND: 1 (default since 2026-10-08) = a training sortie's 0x013A
#: block carries kind 1, so the client opens the Training Result window after
#: the battle (state 0xB); 0 = kind 0, the EXP Gain path every training run
#: took before 10-08.
#: VERIFIED: STATIC 2026-10-08. STATE 0xB ONLY SWAPS A WINDOW. States 7 and
#: 0xA..0xE all run the one result machine 0x61176C40 (lobby state table
#: 0x6117DCC0). Sub-state 0 (0x61176CE0) for 0xB opens the result window
#: (0x610D1680) and goes to sub-state 5, which waits for it to close and joins
#: sub-state 6, the common tail state 7 reaches after its EXP Gain windows
#: (sub-states 1..4 open windows only, 0x611921E0 / 0x610CBA70, and send
#: nothing). The registration's end scene is read on lobby re-entry by the
#: scene machine (lobby+0x69C6 at 0x610064B1), outside the result machine, so
#: the byte-128 chain is untouched. A training battle pays nothing (SE's rule,
#: FMO_TRAINING_PAY), so the skipped EXP Gain window had nothing to show.
KIND = _env_int("FMO_TRAINING_KIND", "1") != 0
#: FMO_TRAINING_PAY: 0 (default, SE's rule) = a training battle pays no exp,
#: contribution or H$ (settlement.settle_battle); 1 = paid like any battle.
TRAINING_PAY = _env_int("FMO_TRAINING_PAY", "0") != 0
KILL_SCORE = _env_int("FMO_TRAINING_KILL_SCORE", "100")
WIN_SCORE = _env_int("FMO_TRAINING_WIN_SCORE", "500")
#: How many results the server keeps per pilot (its own copy, for the board).
KEEP = ROWS

# --------------------------------------------------------------------------- #
# THE BATTLE'S END SCENE (kind 4) AND THE REGISTRATION TRAINING (2026-10-08)
# --------------------------------------------------------------------------- #
#: VERIFIED: STATIC 2026-10-08. WHO PICKS THE SCENE AFTER A BATTLE. The lobby
#: scene machine, back from a battle, first tries the zone's LEV entry script
#: (0x61006413: selector 0x610F6ED0(MapKind, kind 2)); when that does not run
#: it reads lobby+0x69C6 (0x610064B1) and, when non-zero, calls
#: 0x610F6ED0(that id, kind 4, 0) and zeroes it (0x610064CB / 0x610064D2).
#: Kind 4 walks the zone's event table for a kind-4 row with that key
#: (0x610ED240(id, 4)) and runs its script in scene 5. lobby+0x69C6 is the
#: FIRST DWORD OF THE 0x014C RESULT BLOCK (payload +0x104, copied 0x5D dwords
#: by the battle-end arm, zeroed by the lobby reset 0x6117A2CF). No other
#: client code reads it. So the end scene is SERVER-AUTHORED, one dword.
#:
#: KEY: WHERE THE ID COMES FROM. The training sortie syscall 0xE30A
#: (0x610FC060, script window {1, 0x2710} at SCP 0x8074 0x6266..0x627e)
#: fills a pending-sortie struct at wm+0x14F8 (+0 the sector tile, +8 the
#: window's 0x2710, +0xC its 1, +0x10 create 2 or 3 by lobby+0x8BE), and its
#: task 0x610FBB70 first sends the script server call EVENT 102 with
#: p2 = tile / 1000, p3 = tile % 1000, p4 = struct+0xC (1), p5 = struct+8
#: (0x2710) (0x610FBEE2..0x610FBF19 -> 0x610F9CB0), polls it, and only then
#: sends the 0x0139. So the client TELLS the server the battle's end scene in
#: 102's p5 and SE's server handed it back in the 0x014C block. The war map's
#: 102s seen live (09-25, 10-07) carry p4 = p5 = 0: an ordinary battle has
#: no end scene. 0x2710 is D39..D47's kind-4 row `tyu_battle_end`, SCP 0x8084
#: ("The training is a success"), which calls 104 [128] (byte 128 1 -> 2).
#:
#: INFERRED: only a WON battle names it. 0x8084 says "success" and steps byte
#: 128 unconditionally, and the First Sergeant's case 1 ("you failed that
#: training... one more chance", 0x8074 0x63a8) can only be reached when a
#: lost training leaves byte 128 at 1. That case re-fires the training
#: sortie itself (0xE30A at 0x67ca / 0x67ea, no 104), so the retry arrives
#: as another create 3 with byte 128 == 1.
#: FMO_BATTLE_END_SCENE=0 serves block +0x00 = 0 always (the pre-10-08 wire:
#: the training never registers anyone).
SORTIE_CALL_EVENT = 102
END_SCENE_P = 4                       # p5, the struct's +8
END_SCENE_ARG_P = 3                   # p4, the struct's +0xC
BLOCK_END_SCENE = 0x00                # block +0x00 = payload +0x104 = lobby+0x69C6
TRAINING_END_SCENE = 0x2710           # kind-4 key, SCP 0x8084 tyu_battle_end
END_SCENE = _env_int("FMO_BATTLE_END_SCENE", "1") != 0
#: how long a 102 waits for its 0x0139 (the task sends them back to back)
END_SCENE_FRESH = 300
#: KEY: THE TRAINING BATTLEFIELD. The client's training tile is in the ARE
#: table's selector 97 "Tutorial Sector" rows (tile = row * 1000 + 1011 + a
#: random 0..19, +10000 for U.S.N., 0x610FC09B..0x610FC0C1; the live 09-05
#: sortie asked for 3030), which carry no battle map: SE's server chose it.
#: The training-ground rows of the same table (selectors 98 / 99 "Training
#: Sector", 訓練場) are all battle map 70, and the training's end scene row
#: (key 0x2710) loads map 70 for its stage (row +0x4C, 0x610F70EA). OURS on
#: that evidence: the registration and retraining sorties are served on map 70
#: (on disk, and in the console's type-1 set). FMO_TRAINING_MAPNO=0 serves the
#: old map (the sector the war map last opened, else FMO_SORTIE_MAPNO).
TRAINING_MAPNO = _env_int("FMO_TRAINING_MAPNO", "70")
#: KEY: THE TRAINING ENEMIES. The sergeant's brief (AH/F99/D08) says only
#: that the training covers the wanzer's basic controls and the chosen job's
#: traits; no enemy count or type is written anywhere we hold. OURS: the
#: usual FMO_BATTLE_ENEMIES squad dressed at NPC level
#: FMO_TRAINING_ENEMY_LEVEL (default 1: the weakest NPC set, npc60-1, in
#: fmo-npc-loadouts.tsv) instead of FMO_ENEMY_LEVEL's 15 (squad.enemy_level_for).
ENEMY_LEVEL = _env_int("FMO_TRAINING_ENEMY_LEVEL", "1")
#: the registration byte and its steps (scriptcall.REGISTRATION_BYTE)
REG_BYTE = 128
REG_UNTRAINED, REG_SENT, REG_PASSED, REG_DONE = 0, 1, 2, 99
CREATE_TRAINING = 3


def training_mapno(q):
    """The MapNo (str) a training sortie (create 2 or 3) is served on, or None
    for any other sortie or with FMO_TRAINING_MAPNO=0."""
    if not (is_training((q or {}).get("create")) and TRAINING_MAPNO > 0):
        return None
    return str(TRAINING_MAPNO)


def registration_refusal(flags, create):
    """Why an UNREGISTERED pilot (byte 128 != 99) may not take this training
    sortie, or None. Only create 3 (the registration training) is judged:
    the sergeant sends it after 104 [128] has moved the byte to 1 (0x8074
    0x6266: 0xE30A runs only when that call answered), and his "one more
    chance" re-sends it on 1. On 0 the pilot has not been sent; on 2 the
    training is passed and the report (105) is what is owed. Create 2 (the
    penalty retraining) is penalty.py's. Pure."""
    if create != CREATE_TRAINING:
        return None
    b = flags[REG_BYTE] if flags is not None and len(flags) > REG_BYTE else 0
    if b in (REG_SENT, REG_DONE):
        return None
    if b == REG_PASSED:
        return ("byte 128 = 2: the training is already passed; the First "
                "Sergeant's report (105 [128]) is what is owed, not another run")
    return (f"byte 128 = {b}: the First Sergeant has not sent this pilot "
            f"(his 104 [128] moves it to 1 before 0xE30A fires the sortie)")


def note_script_call(sess, ev, ps):
    """A 102 (the script's sortie call, see above): keep its end scene for the
    0x0139 that follows. Any other event: nothing."""
    if ev != SORTIE_CALL_EVENT or not ps or len(ps) <= END_SCENE_P:
        return
    sess.pending_end_scene = {"scene": int(ps[END_SCENE_P]),
                              "arg": int(ps[END_SCENE_ARG_P]),
                              "tile": int(ps[1]) * 1000 + int(ps[2]),
                              "at": time.time()}
    if ps[END_SCENE_P]:
        log(f"{sess.peer}   SORTIE CALL 102: tile {ps[1] * 1000 + ps[2]}, end scene "
            f"{ps[END_SCENE_P]:#x} (p5; p4 = {ps[END_SCENE_ARG_P]}) -- kept for the "
            f"0x0139 that follows; a WON battle hands it back at 0x014C block +0x00")


def end_scene_for_grant(sess, q, now=None):
    """The kind-4 end scene for the sortie being granted: the preceding 102's
    p5 when it is fresh, else for the registration training (create 3) the
    training scene 0x2710 (OURS: a fallback for a 102 we did not see).
    Consumes the pending 102. 0 = none."""
    now = time.time() if now is None else now
    pend = getattr(sess, "pending_end_scene", None)
    sess.pending_end_scene = None
    if not END_SCENE:
        return 0
    if pend and now - pend.get("at", 0) <= END_SCENE_FRESH and pend.get("scene"):
        return int(pend["scene"])
    if (q or {}).get("create") == CREATE_TRAINING:
        return TRAINING_END_SCENE
    return 0


def end_scene_block(scene, block=None):
    """The 372-byte result block with +0x00 = `scene` (over `block`)."""
    b = bytearray(block or bytes(BLOCK_LEN))
    struct.pack_into("<I", b, BLOCK_END_SCENE, int(scene) & 0xFFFFFFFF)
    return bytes(b)


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
        bst = referee.battle_state(sess.battle_key())
        bst["training"] = on
        # the battle's end scene (see END_SCENE): from the 102 just before
        bst["end_scene"] = end_scene_for_grant(sess, q)
        if bst["end_scene"]:
            log(f"{sess.peer}   END SCENE: create {q.get('create')} sortie, kind-4 "
                f"scene {bst['end_scene']:#x} kept for the battle end (a WIN serves it "
                f"at 0x014C block +0x00 -> lobby+0x69C6 -> 0x610064CB)")
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
    """The 0x014C kwargs (block/tail) for this battle's end: the training
    ranking feed (_ranking_fields) and, on a WIN, the end scene the sortie's
    102 named at block +0x00. {} when there is neither. Never raises."""
    f = _ranking_fields(sess, st)
    try:
        from . import referee
        bst = referee.BATTLE_STATE.get(sess.battle_key()) or {}
        scene = int(bst.get("end_scene") or 0)
        if not scene:
            return f
        if not st.get("won"):
            log(f"{sess.peer}   END SCENE: the battle was LOST -- block +0x00 stays 0, "
                f"no kind-4 scene {scene:#x}"
                + ("; byte 128 stays 1, so the First Sergeant plays 'one more "
                   "chance' and re-sends the training sortie (0x8074 0x63a8)"
                   if scene == TRAINING_END_SCENE else ""))
            return f
        f = dict(f)
        f["block"] = end_scene_block(scene, f.get("block"))
        f.setdefault("tail", bytes(TAIL_LEN))
        log(f"{sess.peer}   END SCENE: WON -> 0x014C block +0x00 = {scene:#x} "
            f"(lobby+0x69C6); back in the lobby 0x610064CB runs 0x610F6ED0({scene:#x}, "
            f"kind 4)"
            + (" = 0x8084 tyu_battle_end, which calls 104 [128] (1 -> 2)"
               if scene == TRAINING_END_SCENE else ""))
        return f
    except Exception as e:              # a scene must never cost the battle end
        log(f"{getattr(sess, 'peer', '?')}   WARNING: end scene not served ({e!r})")
        return f


def _ranking_fields(sess, st):
    """The training ranking's block/tail for a training sortie's end, {} for
    any other battle. Also files the result on the pilot. Never raises."""
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
