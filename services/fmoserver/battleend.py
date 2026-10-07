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
#:   +0x0F5 u8   NEW HANGAR RANK -> lobby+0x8BD (0x014A payload+0x39). A SET,
#:           not an add: when it differs from the current byte, 0x6117E409
#:           writes it, after 8:63 "Your hangar rank is now %d." / 8:64 "Your
#:           maximum item capacity is now %d." for whichever of the table
#:           0x61399988 columns (0x611E3F20 wanzers / 0x611E3F50 items)
#:           changed; skipped whole when 0x611F1660() != 0. Filled from
#:           hangar.hangar_rank_at_battle_end (FMO_HANGAR_JOB_LEVEL), which
#:           banks the same value 0x014A serves -- a 0 here would demote.
#:   +0x104  372 B  THE RESULT BLOCK -> lobby+0x69C6 (0x5D dwords), only when
#:           HAS BLOCK and [lobby+0x24] != 5. Fields the arm reads INSIDE it:
#:           +0x108 u32 RESULT = the WINNING SIDE (1 or 2, 0 = nobody): won
#:           when it equals +0x144 (the pilot's own side) for side 1, else
#:           when it equals 2 (0x6117E5CA; script call 0xE230 at 0x610FA3B0
#:           makes the same test against the nation byte lobby+0x8B4). The
#:           won flag picks 0x610E6E70 over 0x610E6EA0 at the transition, and
#:           those two hooks are the ONLY battle-end title the client has:
#:           won -> HUD "OBJECTIVES" / "COMPLETE" / "VICTORY" (0x61237A20,
#:           strings 0x61347DE0..DF4) plus radio 79:17 when the mission block
#:           lobby+0x5C7E+0x50 is set, else 79:16; lost -> "OBJECTIVES" /
#:           "FAILED" / "DEFEAT" plus radio 79:18. Nothing in the 0x014C,
#:           the 0x015A or the mission block selects a mission, arena or
#:           draw variant: an arena win/loss shows the same banner as a
#:           sortie, and a draw can only be shown as a loss.
#:           +0x10C u8 FLAGS: bit0 = SKIP the experience rows; bit0 AND bit1
#:           (= 3) also skip the post-battle "EXP Gain" window (0x610CBA70,
#:           74:0..5 contribution / direct / indirect / action exp, sortie /
#:           kill contribution), tested at 0x61176FD7 on lobby+0x69CE --
#:           every other battle end shows it, arena (lobby state 0xD) included;
#:           +0x110 u32 == 1 -> 0x610D1C30(block, tail) (NOT the replay: a top-10
#:           record/ranking table, 0x610D1580; the Battle Review is
#:           recorded client-side, see MB_START_GAMETIME) (was: a replay/record
#:           hook behind global 0x613B9028); +0x140 bytes indexed by +0x144;
#:           +0x17E u8 -> 8:108 "An experience bonus from the operation
#:           conditions was applied."; block+0x8D (= lobby+0x6A53) nonzero
#:           -> lobby+0x6E42 = 1.
#:   +0x27C  5 x 16 B  per-class rows, copied into the matching lobby+0x7C53
#:           slot (in-battle only, [lobby+0x30] != 0). Row i belongs to exp
#:           row i: +4 direct, +8 indirect exp; the EXP Gain window draws the
#:           class's whole gain and colours +4 / +8 of it as direct / indirect,
#:           the rest as action exp (0x610CC17D), so all-zero rows show the
#:           whole gain as action exp.
#:   +0x2CC  72 B  THE TAIL -> lobby+0x7D13 (0x12 dwords): +0x2CC u32 join-in
#:           base (nonzero AND +0x2DF > 100 -> 8:65 "Join-in time bonus:
#:           +%d%%"); +0x2D0 u32 / +0x2D8 u32 the EXP Gain window's split of
#:           the contribution gain (74:4 sortie / 74:5 kill; which is which is
#:           a labelled guess, both 0 = no split drawn, which is what we send);
#:           +0x2D4 u32 / +0x2F0 u32 either nonzero -> 8:62 mission
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
S14C_FLAG_NO_EXP = 0x01              # skip the experience rows
S14C_FLAGS_NO_SCREEN = 0x03          # ... and the EXP Gain window (0x61176FD7)
S14C_REPLAY = 0x110
S14C_SURV_TABLE = 0x140
S14C_SURV_IDX = 0x144
S14C_OP_BONUS = 0x17E
S14C_NEXT_BATTLE = 0x8D                # within the block
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
#: KEY: PILOT EXP COMES FROM JOB EXP (2026-09-30). The setup tutorial (AI/F00/D94
#: 85, 86, 20): battle exp is split between the main and support jobs, a job
#: that is not set gets none, and "raising each job level also raises your
#: pilot level". The client derives nothing: Pilot level is the class-12 row's
#: own exp (0x611782B0) and the 0x014C arm adds each row to the kind it names
#: (0x6117E52C), so SE's server sent Pilot exp as its own row. Ours: the Pilot
#: row is FMO_PILOT_EXP_PCT percent (default 100) of the job exp the battle
#: paid (kinds 1..8). 0 = no Pilot row, as before.
PILOT_EXP_PCT = _env_int("FMO_PILOT_EXP_PCT", "100")
PILOT_KIND = 12
JOB_KINDS = range(1, 9)
#: FMO_EXP_PACE (battles per level; 0 = off, the old flat rows only). The D15
#: curve's steps grow from 82,800 (Lv 2) to ~19M (Lv 50), so no flat amount can
#: carry a pilot through the campaign; SE's exp grew with the fight (their 2005
#: notes tune it by sector and zone). OUR stand-in: a WIN pays the jobs one
#: Pilot-level step divided by the pace, at the pilot's current Pilot level, a
#: loss half that -- so a level takes about `pace` wins at every level. It goes
#: to the pilot's SET jobs (inventory.set_jobs, the 0x0167 tail): the main job
#: takes FMO_EXP_MAIN_PCT percent (default 50) and the support jobs share the
#: rest; a lone main job takes all of it. A pilot with no job set (a fresh one:
#: 0x0166 serves the tail as zeros) is paid in FMO_EXP_JOBS (default 3, the
#: Mechanic prod has paid since 09-10). The tutorial says exp is split between
#: main and support; the ratio is ours. A guess to tune, not SE's numbers.
EXP_PACE = _env_int("FMO_EXP_PACE", "0")
EXP_JOBS = os.environ.get("FMO_EXP_JOBS", "").strip() or "3"
EXP_MAIN_PCT = _env_int("FMO_EXP_MAIN_PCT", "50")
#: KEY: THE SECTOR SCALES THE PACED EXP (2026-10-06). SE tuned exp by where
#: the fight was, not by a flat amount: update 050628gp4sc1:26-27 「統制区および
#: 占領区のNPC戦エリアにおいて、セクターの地形による獲得経験値量の変化が大きく
#: なるよう調整しました。最大で、これまでの2倍程度の経験値を得られるセクターも
#: 存在します」 (HQ and Occupied NPC areas: up to about twice the exp by sector),
#: and 050815yi0hz6:35 「激戦区での取得経験値が調整され、より多くの経験値が取得
#: できるようになりました」 (the Frontline pays more). Their per-sector numbers
#: are not on any page we hold, and the terrain byte is unbound, so the key is
#: the battle's NPC level (squad.enemy_level_for: 5 x B.G.Cost, else 5 x the
#: NPC rank the war map shows, the difficulty the player picked the sector by).
#: FMO_EXP_SECTOR_PCT = the percent paid at NPC level >= SECTOR_EXP_CAP_LEVEL
#: (25 = rank 5), linear from 100 at level 0; FMO_EXP_FRONT_PCT multiplies it
#: on the Frontline (zone kind 5). 100 / 100 = off, the flat pace as before.
#: The shape (x2 at the hardest NPC sector, more on the Frontline) is SE's;
#: the line between and the Frontline factor are OURS.
EXP_SECTOR_PCT = _env_int("FMO_EXP_SECTOR_PCT", "100")
EXP_FRONT_PCT = _env_int("FMO_EXP_FRONT_PCT", "100")
SECTOR_EXP_CAP_LEVEL = 25
FRONTLINE_ZONE_KIND = 5


def parse_exp_jobs(spec):
    """'3' / '1,5' -> [kinds]; job kinds only (1..8)."""
    out = []
    for piece in (spec or "").split(","):
        piece = piece.strip()
        if not piece:
            continue
        k = int(piece, 0)
        if k not in JOB_KINDS:
            raise ValueError(f"FMO_EXP_JOBS kind {k}: a job is 1..8")
        if k not in out:
            out.append(k)
    return out


def paced_exp_rows(level, won, curve, jobs, pace=None, main_pct=None):
    """The paced battle exp for a pilot at Pilot `level`: one level step /
    pace for a win, half for a loss. `jobs` is main first: the main job takes
    main_pct percent and the rest share the remainder evenly (a lone job takes
    all). Pure. -> [(kind, amount)]."""
    pace = EXP_PACE if pace is None else pace
    main_pct = EXP_MAIN_PCT if main_pct is None else main_pct
    if pace <= 0 or not jobs or not curve or level >= len(curve):
        return []
    step = curve[level] - curve[level - 1] if level >= 1 else curve[0]
    total = step // pace if won else step // (2 * pace)
    if len(jobs) == 1:
        rows = [(jobs[0], total)]
    else:
        main = total * max(0, min(100, main_pct)) // 100
        each = (total - main) // (len(jobs) - 1)
        rows = [(jobs[0], main)] + [(k, each) for k in jobs[1:]]
    return [(k, a) for k, a in rows if a > 0]


def sector_exp_pct(npc_level, zone=None, sector_pct=None, front_pct=None):
    """The percent the paced exp is paid at for a battle at `npc_level` in
    zone `zone` (a selector: 505/509/513 are the Frontline): 100 at level 0,
    rising linearly to `sector_pct` at SECTOR_EXP_CAP_LEVEL and above, times
    `front_pct` percent on the Frontline. Pure."""
    sp = EXP_SECTOR_PCT if sector_pct is None else sector_pct
    fp = EXP_FRONT_PCT if front_pct is None else front_pct
    lv = max(0, min(int(npc_level or 0), SECTOR_EXP_CAP_LEVEL))
    pct = 100 + (int(sp) - 100) * lv // SECTOR_EXP_CAP_LEVEL
    if zone is not None and int(zone) // 100 == FRONTLINE_ZONE_KIND:
        pct = pct * int(fp) // 100
    return max(0, pct)


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
               kill_exp=None, pilot_pct=None):
    """What one battle pays, from what happened in it. Pure.

    `kills` is the count of distinct enemies destroyed, `won` the verdict,
    money/contrib/exp_rows the flat per-sortie pay. The per-kill and win knobs
    default to the module's. Returns {money, contribution, exp_rows,
    kill_bonus_hs, parts} where parts is a readable breakdown for the log.
    Experience rows are merged by kind (the arm walks at most S14C_EXP_MAX),
    and the Pilot row (kind 12) gets `pilot_pct` percent of the job exp on top
    of any Pilot exp named directly; it always keeps its slot."""
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
    pp = PILOT_EXP_PCT if pilot_pct is None else pilot_pct
    jobs_exp = sum(a for k, a in merged.items() if k in JOB_KINDS)
    if pp > 0 and jobs_exp > 0:
        merged[PILOT_KIND] = merged.get(PILOT_KIND, 0) + jobs_exp * pp // 100
        parts.append(f"Pilot exp +{jobs_exp * pp // 100} ({pp}% of the job exp)")
    pilot = merged.pop(PILOT_KIND, 0)
    rows = list(merged.items())[:S14C_EXP_MAX - (1 if pilot else 0)]
    if pilot:
        rows.append((PILOT_KIND, pilot))
    bonus = kills * kb if kb > 0 else 0
    if bonus:
        parts.append(f"Kill bonus H$ {bonus} owed at the Personnel Officer")
    return {"money": money, "contribution": contrib, "exp_rows": rows,
            "kill_bonus_hs": bonus, "parts": parts}


def platoon_exp_rows(rows, pct):
    """The battle's exp rows with the platoon bonus applied (see
    battlegroups.platoon_exp_pct). EVERY row is scaled by the same percent --
    the job rows and the Pilot row (kind 12, itself FMO_PILOT_EXP_PCT of the
    job exp in battle_pay) alike -- so the Pilot row stays the same share of
    the job exp it was before the bonus. Pure."""
    if pct == 100:
        return list(rows)
    return [(k, int(a) * int(pct) // 100) for k, a in rows]


def battle_end_body(hangar_rank=0, contrib_new=0, contrib_old=0, exp_rows=(),
                    won=True, platoon_pct=0, auto_disband=0, victory=0,
                    control=0, counterattack=0, participation=0, join_pct=0,
                    platoon_money=0, platoon_payer=0, op_bonus=0,
                    has_block=True, block=b"", tail=b"", next_battle=False,
                    no_screen=False):
    """The 0x014C body. `block` / `tail` are raw overrides for the 372-B result
    block and the 72-B tail; the named fields are written OVER them, so a
    captured block can be replayed with only the verdict authored.
    `no_screen` sets +0x10C = 3: no experience rows and no EXP Gain window
    after the battle (for a battle end that pays nothing to show)."""
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
    if no_screen:
        b[S14C_FLAGS] = S14C_FLAGS_NO_SCREEN
    b[S14C_OP_BONUS] = 1 if op_bonus else 0
    # result block +0x8D (= lobby+0x6A53, "HasNextBattleWin"): nonzero sets
    # lobby+0x6E42, and after an ARENA battle (lobby state 0xD) the client
    # offers the streak's next battle and sends 0x01BB (coliseum.py)
    if next_battle:
        b[S14C_BLOCK + S14C_NEXT_BATTLE] = 1
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
