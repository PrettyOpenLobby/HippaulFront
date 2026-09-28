"""The battle end push (0x014C): end triggers, objectives, kills and battle pay."""
import os
import struct
from .knobs import _env_float, _env_int


#: 0x014C -- THE BATTLE END (arm 0x6117E33B, BATTLE gate). SE's own debug
#: strings: 0x6133BC60 "戦闘終了。ロビーマップに戻ります。" ("battle over,
#: returning to the lobby map") on the path that sets [lobby+0x24] = 6, and
#: 0x6133BC30 "ロビーマップ中にバトルが終了しました。" ("the battle ended
#: while in the lobby map") on the path that sets [lobby+0x3C] = 1. This is
#: how a mission ENDS from the server side; 0x013D (withdraw) is the client
#: leaving on its own. It is also the RESULT SCREEN's feed -- every message
#: it can post is a reward line:
#:   +0x000  5 x { u8 kind, pad3, u32 amount }  CLASS EXPERIENCE rows: kind
#:           is matched against the 12-row table at lobby+0xF08 (= 0x014A
#:           payload+0x6AC, S14A_BLOCK2), amount is ADDED to row+4 and
#:           0x611E40A0(rank-1, kind-1, exp) recomputes the level byte at
#:           row+1. "%s経験値ゲット(%d)". kind 3 (the Mechanic) additionally
#:           posts 8:52 when [+0x27C row+0x0C] and the +0x140[+0x144] byte are
#:           set. Total > 0 -> 8:46 "Gained experience." or, won with
#:           +0x0F2 > 100, 8:45 "Gained experience (platoon bonus %d%%)".
#:   +0x0E8 u32  contribution NEW -> lobby+0xFC8   } NEW < OLD -> 8:56
#:   +0x0EC u32  contribution OLD -> lobby+0xFCC   } "Contribution decreased";
#:           NEW > OLD -> 8:55 (control bonus, +0x2DD) / 8:54 (victory bonus,
#:           +0x2DC); equal -> nothing.
#:   +0x0F0 u8   HAS BLOCK. WARNING: 0 = the arm returns before the scene change:
#:           messages only, no return to the lobby. 1 = copy the block, reset
#:           the 12 class slots at lobby+0x7C53, and transition.
#:   +0x0F2 s16  platoon experience bonus percent (>100 prints pct-100)
#:   +0x0F4 u8   -> 8:58 "The battle group will be automatically disbanded..."
#:   +0x0F5 u8   NEW HANGAR RANK -> lobby+0x8BD (0x014A payload+0x39; we serve
#:           0). Differs from the current byte -> 8:63 "Your hangar rank is
#:           now %d." / 8:64 "Your maximum item capacity is now %d." via
#:           0x611E3F20 / 0x611E3F50, unless 0x611F1660() says otherwise.
#:   +0x104  372 B  THE RESULT BLOCK -> lobby+0x69C6 (0x5D dwords), only when
#:           HAS BLOCK and [lobby+0x24] != 5. Fields the arm reads INSIDE it:
#:           +0x108 u32 RESULT: won when it equals 1 with +0x144 == 1, else
#:           when it equals 2 (the won flag picks 0x610E6E70 over 0x610E6EA0
#:           at the transition); +0x10C u8 bit0 = SKIP the experience rows;
#:           +0x110 u32 == 1 -> 0x610D1C30(block, tail) (NOT the replay: a top-10
#:           record/ranking table, 0x610D1580; the Battle Review is
#:           recorded client-side, see MB_START_GAMETIME) (was: a replay/record
#:           hook behind global 0x613B9028); +0x140 bytes indexed by +0x144;
#:           +0x17E u8 -> 8:108 "An experience bonus from the operation
#:           conditions was applied."; block+0x8D (= lobby+0x6A53) nonzero
#:           -> lobby+0x6E42 = 1.
#:   +0x27C  5 x 16 B  per-class rows, copied into the matching lobby+0x7C53
#:           slot (in-battle only, [lobby+0x30] != 0).
#:   +0x2CC  72 B  THE TAIL -> lobby+0x7D13 (0x12 dwords): +0x2CC u32 join-in
#:           base (nonzero AND +0x2DF > 100 -> 8:65 "Join-in time bonus:
#:           +%d%%"); +0x2D4 u32 / +0x2F0 u32 either nonzero -> 8:62 mission
#:           participation bonus; +0x2DC u8 victory, +0x2DD u8 control,
#:           +0x2DE u8 counterattack (with +0x2CC != 0 -> 8:60); +0x2DF u8
#:           join-in percent; +0x2EC u32 H$ platoon bonus with +0x2E8 u32 the
#:           payer id: == lobby+0x1DC (self) -> 8:72 "refunded", else 8:61
#:           "Received H$ %d as a platoon bonus." (a MESSAGE -- the wallet is
#:           not touched here; pay it through 0x015A).
#:   The transition (HAS BLOCK): [lobby+0x30] != 0 (a battle map is up) ->
#:   [lobby+0x2C] = 0, [lobby+0x24] = 6, lobby+0x6E3E = clock, [lobby+0x40] =
#:   0, then the won/lost hook; else [lobby+0x3C] = 1. Minimum body 0x314.
#: WARNING: NOT CONFIRMED IN A LIVE SESSION. What this should do on screen: the reward lines, then
#: the client leaves scene 4 for the lobby on its own, with no 0x013D on the
#: wire. If the client stays in the battle, read [lobby+0x24] first: 5 skips
#: the block and 0/3 fail the gate.
MSG_BATTLE_END = 0x014C
S14C_LEN = 0x314
S14C_EXP_ROWS = 0x000
S14C_EXP_ROW_LEN = 0x08
S14C_EXP_MAX = 5
S14C_CONTRIB_NEW = 0x0E8
S14C_CONTRIB_OLD = 0x0EC
S14C_HAS_BLOCK = 0x0F0
S14C_PLATOON_PCT = 0x0F2
S14C_AUTO_DISBAND = 0x0F4
S14C_HANGAR_RANK = 0x0F5
S14C_BLOCK = 0x104
S14C_BLOCK_LEN = 0x5D * 4
S14C_RESULT = 0x108
S14C_FLAGS = 0x10C
S14C_REPLAY = 0x110
S14C_SURV_TABLE = 0x140
S14C_SURV_IDX = 0x144
S14C_OP_BONUS = 0x17E
S14C_CLASS_ROWS = 0x27C
S14C_CLASS_ROW_LEN = 0x10
S14C_TAIL = 0x2CC
S14C_TAIL_LEN = 0x12 * 4
S14C_JOIN_BASE = 0x2CC
S14C_PART_A = 0x2D4
S14C_VICTORY = 0x2DC
S14C_CONTROL = 0x2DD
S14C_COUNTER = 0x2DE
S14C_JOIN_PCT = 0x2DF
S14C_PLATOON_PAYER = 0x2E8
S14C_PLATOON_MONEY = 0x2EC
S14C_PART_B = 0x2F0
#: FMO_BATTLE_END: 0 (default) = never. N>0 = N seconds after a granted sortie
#: (0x013A), the next keepalive carries ONE 0x014C ending the battle. This is
#: a probe of the return path, not a mission engine: the result is whatever
#: the three knobs below say. FMO_BATTLE_END_WON=0 loses; FMO_BATTLE_END_CONTRIB
#: is a contribution delta (banked through credit_money first, like 0x015A);
#: FMO_BATTLE_END_EXP="kind:amount,..." fills the experience rows (kinds are
#: the lobby+0xF08 table's row kinds, 1..12; start with ONE small row).
#: FMO_BATTLE_END is a comma list of TRIGGERS; the first to fire ends the
#: battle with one 0x014C, checked on every keepalive (so up to ~15 s late):
#:   <N>      N seconds after the sortie grant (the timer probe)
#:   escape   the client's cmd 139/137 EMERGENCY ESCAPE on the battle channel
#:            -- the eject button; the one trigger the PLAYER owns. Ends as a
#:            LOSS unless FMO_BATTLE_END_WON=1 is set explicitly.
#:   limit    FMO_MISSION_TIME seconds after the grant -- the time limit the
#:            block already tells the client about (its 5/1-minute banners
#:            come from +0x4C), ended as a LOSS
#: Unset / 0 = never.
BATTLE_END_SPEC = os.environ.get("FMO_BATTLE_END", "").strip()


def parse_battle_end(spec):
    """'escape,limit,90' -> {'escape', 'limit', 90}; '' or '0' -> set()."""
    out = set()
    for piece in (spec or "").split(","):
        piece = piece.strip().lower()
        if not piece or piece == "0":
            continue
        if piece in ("escape", "limit", "objective"):
            out.add(piece)
            continue
        try:
            n = int(piece, 0)
        except ValueError:
            raise ValueError(f"FMO_BATTLE_END piece {piece!r}: want seconds, "
                             f"'escape' or 'limit'")
        if n > 0:
            out.add(n)
    return out


try:
    BATTLE_END = parse_battle_end(BATTLE_END_SPEC)
    _BATTLE_END_ERR = ""
except ValueError as _e:
    BATTLE_END, _BATTLE_END_ERR = set(), str(_e)
BATTLE_END_WON_SET = bool(os.environ.get("FMO_BATTLE_END_WON", "").strip())
BATTLE_END_WON = (os.environ.get("FMO_BATTLE_END_WON", "").strip() or "1") != "0"
BATTLE_END_CONTRIB = _env_int("FMO_BATTLE_END_CONTRIB", "0")
#: KEY: SE'S DEFEAT CONDITION: "自分の機体が撃破されること" -- your own machine is
#: destroyed (topics/060308, 敗北条件), and the squadron "生還率" counted members
#: who came back WITHOUT being destroyed. So a destroyed pilot's OWN battle ends
#: as a loss; room-mates fight on. Live 2026-09-27: both pilots died, the client
#: offers no Emergency Escape once destroyed, and they sat out the 30-minute
#: limit. N seconds after the pilot's DIED record (checked on the keepalive, so
#: up to ~15 s later). On whenever FMO_BATTLE_END is set; 0 disables.
BATTLE_DEATH_END = _env_float("FMO_BATTLE_DEATH_END", "5")
#: {account key: time.time()} of the pilot's own unit's last DIED record --
#: per ACCOUNT, never per address (two pilots behind one router).
PILOT_DEATHS = {}
BATTLE_END_EXP = os.environ.get("FMO_BATTLE_END_EXP", "").strip()
#: FMO_BATTLE_START: 0 (default) = nothing. 1..3 = once per battle channel,
#: right behind the self-POP, queue BM cmd 138 with that reason, so the client
#: prints SE's own "...The battle begins now." banner (see fmoworld.BM_RECV[138])
#: and stamps block+0x48. The cheapest positive control for the WHOLE
#: server->client battle-manager path: if this banner does not appear, no BM
#: message reaches this client and nothing else in that table can be tested.
BATTLE_START = _env_int("FMO_BATTLE_START", "0")
#: FMO_BATTLE_OBJECTIVE="x,y,z,secs": once per battle channel, after the
#: start, ONE cmd-129 slot-0 marker at that world position with a deadline
#: `secs` after the client's clock as last reported by its keepalive. Bar: a
#: HUD marker with a countdown (RMG D15 "目的地に到着 / 達成まで N 秒").
#: WARNING: A LAYOUT GUESS past the fields that were read -- a probe, default off.
BATTLE_OBJECTIVE_SPEC = os.environ.get("FMO_BATTLE_OBJECTIVE", "").strip()


def parse_battle_objective(spec):
    """'x,y,z,secs' -> ((x, y, z), secs); '' -> None."""
    spec = (spec or "").strip()
    if not spec:
        return None
    parts = [p.strip() for p in spec.split(",")]
    if len(parts) != 4:
        raise ValueError(f"FMO_BATTLE_OBJECTIVE={spec!r}: want x,y,z,secs")
    try:
        x, y, z = (float(p) for p in parts[:3])
        secs = int(parts[3], 0)
    except ValueError:
        raise ValueError(f"FMO_BATTLE_OBJECTIVE={spec!r}: x,y,z are numbers and "
                         f"secs an integer")
    if secs <= 0:
        raise ValueError("FMO_BATTLE_OBJECTIVE: secs must be > 0")
    return (x, y, z), secs


try:
    BATTLE_OBJECTIVE = parse_battle_objective(BATTLE_OBJECTIVE_SPEC)
    _BATTLE_OBJECTIVE_ERR = ""
except ValueError as _e:
    BATTLE_OBJECTIVE, _BATTLE_OBJECTIVE_ERR = None, str(_e)
#: FMO_BATTLE_HIT: 'off' (default) = the client's hit records (cmd 29 / 42) and
#: its damage-table syncs (cmd 128) are logged and dropped, as they always
#: were. WARNING: A shooter applies its own record LOCALLY to every unit but its own
#: (fmoworld.parse_hit's note), so the dummy should die with this OFF -- that
#: is the first thing to check. 'relay' = the BM's job for everyone else:
#: every hit record goes to every other client in the same battle (and back
#: to the shooter, harmlessly -- its own unit is never the target), and the
#: damage sync rides the shooter's alias stream on those clients; a client
#: applies a received record only to ITS OWN unit, which is how a player is
#: damaged by anyone. Nothing is judged or scaled: the client authored the
#: numbers, the server relays them. Bar: a second client sees the dummy's
#: damage; a player can be hit by another player.
BATTLE_HIT = (os.environ.get("FMO_BATTLE_HIT", "").strip() or "off").lower()
#: FMO_OBJECTIVE: the first server-side objective, judged from what the server
#: already knows and ended through FMO_BATTLE_END's 'objective' trigger:
#:   hold:x,z,w,h:secs   the pilot's cmd-240 position stays inside the world-
#:                       unit rect (origin x,z; size w,h) for secs seconds
#:   destroy:<unitid>    a hit record with the DIED flag names that unit
#:                       (the dummy is 0x2222); the record reaches us whether
#:                       or not FMO_BATTLE_HIT relays it
#: The HUD is told through BM cmd 18 kind 1 banners (the same arm the "battle
#: begins" text used) at the start, on entering/leaving the zone, and on
#: completion. Unset = no objective; the battle still ends by escape/limit.
OBJECTIVE_SPEC = os.environ.get("FMO_OBJECTIVE", "").strip()


def parse_objective(spec):
    """'hold:64,64,32,32:45' -> ('hold', (64.0, 64.0, 32.0, 32.0), 45);
    'destroy:0x2222' -> ('destroy', 0x2222, None); '' -> None."""
    spec = (spec or "").strip()
    if not spec:
        return None
    parts = spec.split(":")
    kind = parts[0].strip().lower()
    try:
        if kind == "hold" and len(parts) == 3:
            x, z, w, h = (float(v) for v in parts[1].split(","))
            secs = int(parts[2], 0)
            if w <= 0 or h <= 0 or secs <= 0:
                raise ValueError
            return "hold", (x, z, w, h), secs
        if kind == "destroy" and len(parts) == 2:
            if parts[1].strip().lower() == "all":
                return "destroy", "all", None
            return "destroy", int(parts[1], 0), None
    except ValueError:
        pass
    raise ValueError(f"FMO_OBJECTIVE={spec!r}: want hold:x,z,w,h:secs, "
                     f"destroy:<unitid> or destroy:all")


try:
    OBJECTIVE = parse_objective(OBJECTIVE_SPEC)
    _OBJECTIVE_ERR = ""
except ValueError as _e:
    OBJECTIVE, _OBJECTIVE_ERR = None, str(_e)


def parse_exp_rows(spec):
    """'kind:amount,kind:amount' -> [(kind, amount)]; empty -> []. Raises
    ValueError with the offending piece so a typo is a log line, not a body."""
    out = []
    for piece in (spec or "").split(","):
        piece = piece.strip()
        if not piece:
            continue
        try:
            k, a = piece.split(":")
            k, a = int(k, 0), int(a, 0)
        except ValueError:
            raise ValueError(f"FMO_BATTLE_END_EXP piece {piece!r}: want kind:amount")
        if not 0 < k < 256:
            raise ValueError(f"FMO_BATTLE_END_EXP kind {k}: a u8 matched against "
                             f"the lobby+0xF08 rows, 1..12 on a served table")
        out.append((k, a))
    if len(out) > S14C_EXP_MAX:
        raise ValueError(f"FMO_BATTLE_END_EXP: {len(out)} rows, the arm walks 5")
    return out


#: PERFORMANCE PAY (2026-09-26). Until now one battle paid the same flat
#: FMO_RESULT_MONEY / FMO_RESULT_CONTRIB whatever happened in it, so nothing a
#: pilot did in a fight changed what they got. SE paid two kinds of
#: contribution (intro/flow3.html:15-18): SORTIE contribution for sortieing at
#: all (the flat knob, still paid on a loss or a withdraw) and DESTRUCTION
#: contribution for kills (撃破貢献値) -- FMO_KILL_CONTRIB per enemy destroyed.
#: The paybook has its own "Kill bonus" line (group 11 kind 2, PAY_KILL):
#: FMO_KILL_BONUS_HS per kill is owed there and collected at the Personnel
#: Officer like every other line. FMO_WIN_MONEY / FMO_WIN_CONTRIB are paid on a
#: WIN only; FMO_KILL_EXP ("kind:amount,...") is class exp per kill, SE's
#: "direct exp, shared by every unit involved in a kill" (flow3:9-13).
#: WARNING: SE's amounts are not on any page we hold: every number here is OURS.
#: All default 0 = the flat pay exactly as before.
#: A kill counts only when its target is a unit THIS server popped as an enemy
#: on this sortie (battle_state()["enemies"]): the DIED records the client
#: authors also name the pilot's own unit when it is destroyed.
KILL_CONTRIB = _env_int("FMO_KILL_CONTRIB", "0")
KILL_BONUS_HS = _env_int("FMO_KILL_BONUS_HS", "0")
WIN_MONEY = _env_int("FMO_WIN_MONEY", "0")
WIN_CONTRIB = _env_int("FMO_WIN_CONTRIB", "0")
KILL_EXP = os.environ.get("FMO_KILL_EXP", "").strip()


def battle_kills(st):
    """The distinct enemy UnitIDs destroyed this sortie, in kill order: targets
    of st['kills'] that are in st['enemies']. Pure."""
    enemies = set((st or {}).get("enemies") or ())
    out = []
    for target, _when in (st or {}).get("kills") or ():
        if target in enemies and target not in out:
            out.append(target)
    return out


def battle_pay(kills, won, money=0, contrib=0, exp_rows=(), kill_contrib=None,
               kill_bonus_hs=None, win_money=None, win_contrib=None,
               kill_exp=None):
    """What one battle pays, from what happened in it. Pure.

    `kills` is the count of distinct enemies destroyed, `won` the verdict,
    money/contrib/exp_rows the flat per-sortie pay. The per-kill and win knobs
    default to the module's. Returns {money, contribution, exp_rows,
    kill_bonus_hs, parts} where parts is a readable breakdown for the log.
    Experience rows are merged by kind (the arm walks at most S14C_EXP_MAX)."""
    kc = KILL_CONTRIB if kill_contrib is None else kill_contrib
    kb = KILL_BONUS_HS if kill_bonus_hs is None else kill_bonus_hs
    wm = WIN_MONEY if win_money is None else win_money
    wc = WIN_CONTRIB if win_contrib is None else win_contrib
    kx = parse_exp_rows(KILL_EXP) if kill_exp is None else list(kill_exp)
    kills = max(0, int(kills))
    parts = []
    if money or contrib:
        parts.append(f"sortie H$ {money:+d} / contribution {contrib:+d}")
    if kills and kc:
        contrib += kills * kc
        parts.append(f"{kills} kill(s) x {kc} = destruction contribution "
                     f"{kills * kc:+d}")
    if won and (wm or wc):
        money += wm
        contrib += wc
        parts.append(f"win H$ {wm:+d} / contribution {wc:+d}")
    merged = {}
    for kind, amount in exp_rows:
        merged[int(kind)] = merged.get(int(kind), 0) + int(amount)
    if kills and kx:
        for kind, amount in kx:
            merged[int(kind)] = merged.get(int(kind), 0) + kills * int(amount)
        parts.append(f"kill exp {', '.join(f'{k}:+{kills * a}' for k, a in kx)}")
    rows = list(merged.items())[:S14C_EXP_MAX]
    bonus = kills * kb if kb > 0 else 0
    if bonus:
        parts.append(f"Kill bonus H$ {bonus} owed at the Personnel Officer")
    return {"money": money, "contribution": contrib, "exp_rows": rows,
            "kill_bonus_hs": bonus, "parts": parts}


def battle_end_body(hangar_rank=0, contrib_new=0, contrib_old=0, exp_rows=(),
                    won=True, platoon_pct=0, auto_disband=0, victory=0,
                    control=0, counterattack=0, participation=0, join_pct=0,
                    platoon_money=0, platoon_payer=0, op_bonus=0,
                    has_block=True, block=b"", tail=b""):
    """The 0x014C body. `block` / `tail` are raw overrides for the 372-B result
    block and the 72-B tail; the named fields are written OVER them, so a
    captured block can be replayed with only the verdict authored."""
    if len(exp_rows) > S14C_EXP_MAX:
        raise ValueError(f"0x{MSG_BATTLE_END:04X}: {len(exp_rows)} experience "
                         f"rows, the arm walks {S14C_EXP_MAX}")
    if block and len(block) != S14C_BLOCK_LEN:
        raise ValueError(f"0x{MSG_BATTLE_END:04X}: block is {len(block)}B, the "
                         f"arm copies exactly {S14C_BLOCK_LEN} (`rep movsd 0x5D`)")
    if tail and len(tail) != S14C_TAIL_LEN:
        raise ValueError(f"0x{MSG_BATTLE_END:04X}: tail is {len(tail)}B, the "
                         f"arm copies exactly {S14C_TAIL_LEN} (`rep movsd 0x12`)")
    b = bytearray(S14C_LEN)
    if block:
        b[S14C_BLOCK:S14C_BLOCK + S14C_BLOCK_LEN] = block
    if tail:
        b[S14C_TAIL:S14C_TAIL + S14C_TAIL_LEN] = tail
    for i, (kind, amount) in enumerate(exp_rows):
        at = S14C_EXP_ROWS + i * S14C_EXP_ROW_LEN
        b[at] = int(kind) & 0xFF
        struct.pack_into("<I", b, at + 4, int(amount) & 0xFFFFFFFF)
    struct.pack_into("<I", b, S14C_CONTRIB_NEW, int(contrib_new) & 0xFFFFFFFF)
    struct.pack_into("<I", b, S14C_CONTRIB_OLD, int(contrib_old) & 0xFFFFFFFF)
    b[S14C_HAS_BLOCK] = 1 if has_block else 0
    struct.pack_into("<h", b, S14C_PLATOON_PCT, int(platoon_pct))
    b[S14C_AUTO_DISBAND] = 1 if auto_disband else 0
    b[S14C_HANGAR_RANK] = int(hangar_rank) & 0xFF
    # won: RESULT == 2 with +0x144 == 0 (the `sub eax, 2` arm); lost: 0.
    struct.pack_into("<I", b, S14C_RESULT, 2 if won else 0)
    b[S14C_SURV_IDX] = 0
    b[S14C_OP_BONUS] = 1 if op_bonus else 0
    struct.pack_into("<I", b, S14C_PART_A, 1 if participation else 0)
    b[S14C_VICTORY] = 1 if victory else 0
    b[S14C_CONTROL] = 1 if control else 0
    b[S14C_COUNTER] = 1 if counterattack else 0
    b[S14C_JOIN_PCT] = int(join_pct) & 0xFF
    if join_pct or counterattack:
        struct.pack_into("<I", b, S14C_JOIN_BASE, 1)   # the "!= 0" both need
    struct.pack_into("<I", b, S14C_PLATOON_PAYER, int(platoon_payer) & 0xFFFFFFFF)
    struct.pack_into("<I", b, S14C_PLATOON_MONEY, int(platoon_money) & 0xFFFFFFFF)
    return bytes(b)


def battle_end_packet(conn_id, **fields):
    return packet.build(MSG_BATTLE_END, battle_end_body(**fields), pushes.QUEUE_SEQ, conn_id)


# Called at run time only; imported last so that import cycles resolve.
from . import packet, pushes  # noqa: E402
