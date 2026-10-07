"""Defection (亡命): who may change nations, the refusal the client shows, and
what a defection costs the pilot."""
import struct
import time
from .deps import fmowar
from .knobs import _env_int


# --------------------------------------------------------------------------- #
# DEFECTION -- SE's rules for "Change Nations" (0x01AB opens it, 0x01AA submits)
# --------------------------------------------------------------------------- #
#: KEY: CHANGE NATIONS IS DEFECTION, AND SE RAN IT WITH RULES. Until 2026-09-30
#: the 0x01AA arm flipped `nation_byte` for anyone, any number of times.
#:
#: SE, update 050906 (lines 97-108, the day it shipped; topics 050826 is the
#: same list in advance):
#:     亡命を行うには、以下の条件をクリアしている必要があります。
#:     ・人数の多い国から少ない国への亡命であること。
#:     ※ただし、両軍の人数が拮抗している場合には、相互に亡命が可能です。
#:     ・初等兵以上かつ准尉長以下の階級であること。
#:     ・レベル5以上のキャラクターであること。
#:     ・前回の亡命から30日以上経過していること。
#:     ...
#:     ・亡命前の所持品は定価で換金されますが、一部のアイテムは所持したまま異動
#:       します（アクセサリー・β部隊章）。所属国特有の部隊章など、換金できない
#:       アイテムは没収されます。
#:     ・亡命の際はキャラクターの名前の変更が可能です。
#:     ・亡命を行うと、階級が１ランク低下します。
#: news5570 (2005-09-21): 亡命ができるかどうかの判定を両陣営のパイロットレベルが
#: 15以上の有効な総プレイヤー数で行うように変更しました -- the head count is over
#: pilots of Pilot level 15 and up.
#: topics 060227: 勝敗が決する直前の亡命を防止する為、2006年3月6日(月)12:00より
#: 停戦処理が完了するまでの期間、亡命ができなくなります -- locked in the run-up to
#: a ceasefire.
#:
#: The in-game confirmation (systext 17:176) still says "Captain or above is
#: demoted to Lieutenant", which is the pre-release wording: the shipped rule
#: caps the rank at Chief Warrant Officer and drops ONE step.
DEFECTION = (_env_int("FMO_DEFECTION", "1") != 0)

#: SE's numbers. The rank byte is 0-BASED (fmo-ranks.tsv): 0 Conscript ..
#: 17 Chief Warrant Officer (准尉長), 18 Second Lieutenant.
RANK_MIN = 0
RANK_MAX = 17
PILOT_LEVEL_MIN = 5
COUNT_PILOT_LEVEL = 15
COOLDOWN_DAYS = 30
RANK_DROP = 1
PILOT_KIND = 12                  # the class table's Pilot row (classes.CLASS_NAMES)
DAY = 86400

#: "両軍の人数が拮抗している場合" -- SE never said what counts as even. Ours, to
#: tune: the two head counts are even when they differ by at most this percent
#: of the larger one (0 vs 0 is always even). FMO_DEFECT_EVEN_PCT=0 = only an
#: exact tie is even.
EVEN_PCT = _env_int("FMO_DEFECT_EVEN_PCT", "10")
#: The run-up lock. SE's one example is 2006-03-06 12:00 for a ceasefire in
#: March, with no general rule. Ours, to tune: this many days before the
#: phase's judgement (fmowar.phase_at), through the ceasefire, until the next
#: phase starts. 0 = no lock.
LOCK_DAYS = _env_int("FMO_DEFECT_LOCK_DAYS", "7")

#: KEY: THE REFUSALS ARE THE CLIENT'S OWN SENTENCES. The 0x01AA request object
#: (lobby+0x7506, vtable 0x6133B424, send 0x61172A20) is started by the generic
#: 0x61173210, so it waits for message 1; any other id takes the dispatcher's
#: mismatch arm 0x61172301, which stores the reply's +0x08 word at
#: lobby+0x7D5F and returns -4. The task base (0x61048370) turns -4 in state 3
#: into the error dialog 0x61047A80 -> 0x6116CF50 -> 0x6116CE10(code), and for
#: any code <= -13000 that formatter looks the code up in the table at
#: 0x613955F0 (7-byte rows: s16 code, u8 flag, u32 systext id) and draws THAT
#: sentence. The defection rows, read from the image:
#:     -14121 -> 0xC002006D  2:109  You cannot defect again so soon after defecting.
#:     -14122 -> 0xC002006E  2:110  Defection from O.C.U. to U.S.N. is not available right now.
#:     -14127 -> 0xC002006F  2:111  Defection from U.S.N. to O.C.U. is not available right now.
#:     -14123 -> 0xC0020070  2:112  Your rank is too low.
#:     -14124 -> 0xC0020071  2:113  Your rank is too high.
#:     -14125 -> 0xC0020072  2:114  Your pilot level is too low.
#: Flag 0 = the "Info" caption and no [FMOnnnnn] number. Read statically, not
#: yet seen on a screen.
REFUSE_TOO_SOON = -14121
REFUSE_OCU_TO_USN = -14122
REFUSE_USN_TO_OCU = -14127
REFUSE_RANK_LOW = -14123
REFUSE_RANK_HIGH = -14124
REFUSE_PILOT_LEVEL = -14125
REFUSAL_TEXT = {
    REFUSE_TOO_SOON: "2:109 'You cannot defect again so soon after defecting.'",
    REFUSE_OCU_TO_USN: "2:110 'Defection from O.C.U. to U.S.N. is not available right now.'",
    REFUSE_USN_TO_OCU: "2:111 'Defection from U.S.N. to O.C.U. is not available right now.'",
    REFUSE_RANK_LOW: "2:112 'Your rank is too low.'",
    REFUSE_RANK_HIGH: "2:113 'Your rank is too high.'",
    REFUSE_PILOT_LEVEL: "2:114 'Your pilot level is too low.'",
}


def closed_code(was):
    """The 'not available right now' refusal for leaving nation `was`."""
    return REFUSE_OCU_TO_USN if was == 1 else REFUSE_USN_TO_OCU


def pilot_level(char):
    """Pilot level = the class table's kind-12 level (0x611E40A0 on its exp)."""
    return classes.class_level(classes.class_exp_of(char).get(PILOT_KIND, 0))


def pilot_rank(char):
    return int(economy._econ_value("rank", None, status.START_RANK, "FMO_RANK", char or {})[0] or 0)


def population(rosters, min_level=COUNT_PILOT_LEVEL):
    """{1: n, 2: n}: stored pilots per nation at Pilot level >= min_level
    (news5570: 両陣営のパイロットレベルが15以上の有効な総プレイヤー数).
    `rosters` = [(account, roster)]. Pure."""
    out = {1: 0, 2: 0}
    for _acct, roster in rosters or ():
        for c in roster or ():
            n = popnation.character_nation(c)[0]
            if n in out and pilot_level(c) >= min_level:
                out[n] += 1
    return out


def balance_allows(was, to, counts, even_pct=None):
    """(ok, why): larger nation to smaller, either way when even. Pure."""
    even_pct = EVEN_PCT if even_pct is None else even_pct
    a, b = counts.get(was, 0), counts.get(to, 0)
    if abs(a - b) * 100 <= max(a, b) * even_pct:
        return True, (f"{a} vs {b} pilot(s) at level {COUNT_PILOT_LEVEL}+ is even "
                      f"(within {even_pct}%, FMO_DEFECT_EVEN_PCT): either way")
    if a > b:
        return True, f"{a} pilot(s) at level {COUNT_PILOT_LEVEL}+ leaving for {b}: larger to smaller"
    return False, (f"{a} vs {b} pilot(s) at level {COUNT_PILOT_LEVEL}+: nation {was} is the "
                   f"SMALLER side and the gap is over {even_pct}% (FMO_DEFECT_EVEN_PCT)")


def ceasefire_locked(now, lock_days=None, phase=None):
    """(locked, why): inside [judgement - LOCK_DAYS, next phase start)."""
    lock_days = LOCK_DAYS if lock_days is None else lock_days
    if lock_days <= 0:
        return False, "FMO_DEFECT_LOCK_DAYS=0"
    if phase is None:
        if fmowar is None:
            return False, "no war clock (fmowar not importable)"
        phase = fmowar.phase_at(now)
    n, _start, judge, nxt, _cease = phase
    if not n or judge is None:
        return False, "no phase running"
    if judge - lock_days * DAY <= now < nxt:
        return True, (f"phase {n} is judged at {time.strftime('%Y-%m-%d %H:%MZ', time.gmtime(judge))}; "
                      f"defection is locked from {lock_days} day(s) before it until the next "
                      f"phase starts (FMO_DEFECT_LOCK_DAYS, topics 060227)")
    return False, f"phase {n}, outside the run-up lock"


def defection_verdict(char, was, to, rosters, now=None, phase=None):
    """(code, why): code None = allowed, else the s16 refusal to put in the
    message-2 reply's +0x08. Checks in SE's list order."""
    now = time.time() if now is None else float(now)
    last = char.get("last_defected_at")
    if isinstance(last, (int, float)) and now - last < COOLDOWN_DAYS * DAY:
        return REFUSE_TOO_SOON, (f"last defection {int((now - last) // DAY)} day(s) ago; "
                                 f"SE: {COOLDOWN_DAYS} days or more")
    rank = pilot_rank(char)
    if rank < RANK_MIN:
        return REFUSE_RANK_LOW, f"rank {rank} is below {ranks.rank_name(RANK_MIN)}"
    if rank > RANK_MAX:
        return REFUSE_RANK_HIGH, (f"rank {rank} {ranks.rank_name(rank)} is above "
                                  f"{ranks.rank_name(RANK_MAX)} (SE: Conscript to Chief Warrant Officer)")
    lv = pilot_level(char)
    if lv < PILOT_LEVEL_MIN:
        return REFUSE_PILOT_LEVEL, f"Pilot level {lv}; SE: level {PILOT_LEVEL_MIN} or higher"
    locked, why = ceasefire_locked(now, phase=phase)
    if locked:
        return closed_code(was), why
    ok, why = balance_allows(was, to, population(rosters))
    if not ok:
        return closed_code(was), why
    return None, why


def equipped_serials(char):
    """Every 64-bit serial a stored wanzer setup equips (setup +0x28, 21 x 24)."""
    try:
        block = bytes.fromhex((char or {}).get("setups") or "")
    except ValueError:
        return set()
    out = set()
    for i in range(inventory.SETUP_SLOTS):
        base = i * inventory.SETUP_ENTRY_LEN + inventory.SETUP_ITEM_OFF
        for k in range(inventory.SETUP_ITEMS):
            at = base + k * inventory.INV_ENTRY_LEN
            if at + inventory.INV_ENTRY_LEN > len(block):
                return out
            lo, hi = struct.unpack_from("<II", block, at + inventory.ITEM_SERIAL_LO)
            if lo or hi:
                out.add(lo | (hi << 32))
    return out


def apply_defection(char, now=None):
    """SE's consequences that this server's data holds, applied to `char` in
    place (the nation itself is flipped by the caller). Returns the list of
    what was done, for the log.

    NOT APPLIED, because the data is not here: the buddy list and radio-voice
    registrations (the community side / the client), and nation insignia --
    insignia are registered per SQUADRON (fmostore.squadron_insignia), not
    carried by the pilot, so there is nothing on the pilot to confiscate."""
    now = time.time() if now is None else float(now)
    done = []
    rank = pilot_rank(char)
    new_rank = max(RANK_MIN, rank - RANK_DROP)
    if new_rank != rank:
        char["rank"] = new_rank
        # Contribution that still earns the old rank would promote the pilot
        # straight back at the next service record (0x0175 never demotes but
        # does promote), so it is pulled under the lost rank's bar -- the same
        # 90% line the officer review uses for a demotion.
        contrib = int(economy._econ_value("contribution", None, status.STATUS_CONTRIB,
                                          "FMO_STATUS_CONTRIB", char)[0] or 0)
        cap = servicerecord.review_demoted_contribution(new_rank)
        if contrib > cap:
            char["contribution"] = cap
        done.append(f"rank {rank} {ranks.rank_name(rank)} -> {new_rank} "
                    f"{ranks.rank_name(new_rank)} (SE: one rank lower)"
                    + (f", contribution {contrib} -> {cap} so 0x0175 cannot re-promote"
                       if contrib > cap else ""))
    else:
        done.append(f"rank stays {rank} {ranks.rank_name(rank)} (nothing below it)")
    # Items -> cash at list price. The price on file is the one the shop
    # charged at 0x0168 (pending+4), i.e. SE's list price. Parts equipped in
    # a wanzer setup stay: the setup would otherwise hold serials the next
    # 0x0133 no longer lists, and SE's wording is about the inventory.
    keep_serials = equipped_serials(char)
    items = inventory.stored_items(char)
    sold = [it for it in items if it["serial"] not in keep_serials]
    if sold:
        cash = sum(max(0, int(it.get("price") or 0)) for it in sold)
        char["items"] = [it for it in (char.get("items") or ())
                         if isinstance(it, dict) and int(it.get("serial", -1)) in keep_serials]
        money = economy.wallet_money(char)[0]
        char["money"] = max(0, money) + cash
        done.append(f"{len(sold)} item(s) converted at list price for H$ {cash} "
                    f"(money {money} -> {char['money']}); {len(items) - len(sold)} equipped "
                    f"part(s) kept")
    else:
        done.append("no unequipped items to convert")
    char["last_defected_at"] = int(now)
    done.append(f"next defection allowed after "
                f"{time.strftime('%Y-%m-%d', time.gmtime(now + COOLDOWN_DAYS * DAY))}")
    return done


# Called at run time only; imported last so that import cycles resolve.
from . import classes, economy, inventory, popnation, ranks, servicerecord, status  # noqa: E402
