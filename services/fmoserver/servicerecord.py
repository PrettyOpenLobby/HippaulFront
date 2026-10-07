"""The service record (0x0175 -> 0x0176): paydays, salary, the ceasefire bonus and the officer
review."""
import datetime
import os
import struct
import time
from .knobs import _env_float, _env_int


#: 0x0176 body (payload+0x20..): the 476-B block the mission-result machine
#: copies to itself (0x6119291C, `rep movsd 0x77`) and applies field by field.
S176_CONTRIB_SHOWN = 0x04     # -> machine+0x218, clamped below a ladder threshold
S176_CONTRIB_STORED = 0x08    # -> lobby+0xFC8 (S14A_CONTRIB), same clamp
S176_B0C = 0x0C               # -> lobby+0x8BC (status+0x30), unread by us
S176_RANK = 0x0D              # -> lobby+0x8BB (S14A_RANK)
#: WARNING: +0x0F is the PROMOTION-OUTLOOK index, NOT a rank name (renamed 2026-09-12
#: after the live read). It selects systext GROUP 36 -- 36:1 "highly valued",
#: 36:2 "an order will come soon", 36:3 "not recognised, under review", 36:4
#: "no message from the top brass", 36:11..14 / 21..24 the promotion and
#: demotion review variants -- which 0x61175540 sprintf's (with the nation
#: name) into lobby+0x7C, and the SAME arm then prints to chat (0x611929E7 ->
#: 0x61184330). SE chose it from the pilot's review state; we have none.
#: 0 = the client's own "nothing pending": 36:0 is `$(予備)`, and 0x61175540
#: clears the buffer on a leading '$' or an empty string, so nothing is printed
#: and the script's E315 gate (0x610FB480) answers "no orders pending".
S176_OUTLOOK = 0x0F
S176_RANK_NAME = S176_OUTLOOK   # the old name, kept for readers of older notes
#: FMO_OUTLOOK: empty / 'auto' (default) = the Personnel Officer picks the
#: line from the pilot's real standing (personnel_visit, OUTLOOK_* below); a
#: number forces that group-36 index on every visit (0 = say nothing, the
#: pre-2026-10-07 behaviour). OUTLOOK is the fixed index service_record_block
#: falls back to when the caller passes none.
_OUTLOOK_RAW = os.environ.get("FMO_OUTLOOK", "").strip().lower()
OUTLOOK_AUTO = _OUTLOOK_RAW in ("", "auto")
OUTLOOK = 0 if OUTLOOK_AUTO else _env_int("FMO_OUTLOOK", "0")
#: KEY: +0x10 is the NEXT PAYDAY, a time_t (static 2026-09-12). The machine
#: holds the body at machine+0x34, and 0x61192980 passes [machine+0x44] =
#: body+0x10 to 0x611754D0, which formats it in mode 8 of 0x611E3D00 --
#: "%04d/%02d/%02d %02d:%02d", local time -- into lobby+0x7B7C ("%s"). That
#: buffer is the blank (FMDT token FF 12) in the Personnel Officer's line
#: AH/F98/D64 record 53, "The next payday is ___." We had it named as a
#: money field and the paybook fill wrote its H$ TOTAL here, so the officer announced a
#: payday of 1970/01/01 plus that many seconds ("1970/01/01 09:48").
S176_NEXT_PAYDAY = 0x10


def next_payday_unix(now=None):
    """The next payday: the coming UTC midnight, the same day boundary
    paydays_owed() pays on (a pilot is paid for every UTC day it has not
    been paid yet)."""
    return (int(_now_unix(now) // DAY) + 1) * DAY


def service_record_block(char, ladder=None, now=None, send_rank=None, outlook=None):
    """(0x0176 body, info) for one pilot -- SE's service-record check.

    The rank and contribution come from the CHARACTER (the knob is only the
    seed, exactly as 0x014A resolves them), and the rank is PROMOTED to the
    highest ladder row the contribution meets. Never demotes: a pilot seeded
    above what their contribution earns (every pilot on this server today,
    seeded at FMO_RANK with contribution 0) keeps the seeded rank. Pure --
    the caller banks `info["rank"]` when `info["promoted"]`. `ladder=()`
    disables the promotion (no table, no ladder walk).

    `send_rank` / `outlook` are personnel_visit()'s decision (the Session
    path): the rank byte +0x0D is then exactly that -- no ladder walk here,
    because a +0x0D the client's ordered rank does not match locks the
    mission desk (ranks.orders_pending) -- and +0x0F is that group-36 line."""
    char = char or {}
    ladder = ranks.RANK_LADDER if ladder is None else ladder
    rank, rank_src = economy._econ_value("rank", None, status.START_RANK, "FMO_RANK", char)
    contrib, contrib_src = economy._econ_value("contribution", None, status.STATUS_CONTRIB,
                                               "FMO_STATUS_CONTRIB", char)
    rank = int(rank or 0) & 0xFF
    contrib = int(contrib or 0)
    if send_rank is not None:
        earned = int(send_rank) & 0xFF
        promoted = earned > rank
        new_rank = earned
    else:
        earned = ranks.rank_for_contribution(contrib, ladder) if ladder else rank
        promoted = earned > rank
        new_rank = earned if promoted else rank
    blk = bytearray(S176_BODY_LEN)
    struct.pack_into("<i", blk, S176_CONTRIB_SHOWN, contrib)
    struct.pack_into("<i", blk, S176_CONTRIB_STORED, contrib)
    blk[S176_B0C] = 0
    blk[S176_RANK] = new_rank & 0xFF
    # WARNING: NOT the rank. +0x0F indexes systext GROUP 36, the Personnel Officer's
    # promotion-OUTLOOK lines, and 0x61175540 sprintf's the chosen one into
    # lobby+0x7C -- which the same arm then PRINTS TO CHAT (0x611929E7 ->
    # 0x61184330). Serving the rank byte made a rank-24 pilot read 36:24,
    # "your contribution has been reviewed and the demotion hearing has been
    # cancelled", on every service-record check: a review that never happened.
    # 0 is the client's own "nothing pending": 36:0 is `$(予備)` and 0x61175540
    # CLEARS the buffer for a leading '$' (or an empty string), so no line is
    # printed and E315 (0x610FB480, "is an orders message pending?") answers 0.
    # FMO_OUTLOOK serves a real group-36 index once there is a review state to
    # report -- 4 is "There is no message for you from the top brass."
    blk[S176_OUTLOOK] = (OUTLOOK if outlook is None else int(outlook)) & 0xFF
    # The officer's "next payday" (see S176_NEXT_PAYDAY): only when there IS
    # a salary -- with FMO_SALARY=0 no day ever pays, and 0 keeps the old line.
    struct.pack_into("<I", blk, S176_NEXT_PAYDAY,
                     (next_payday_unix(now) if SALARY else 0) & 0xFFFFFFFF)
    nxt = ranks.rank_threshold(new_rank + 1, ladder) if ladder else None
    return bytes(blk), {
        "rank": new_rank, "rank_was": rank, "rank_src": rank_src,
        "promoted": promoted,
        "threshold": ranks.rank_threshold(new_rank, ladder) if ladder else None,
        "next_threshold": nxt if nxt is not None else "no next row",
        "contribution": contrib, "contribution_src": contrib_src,
    }


#: KEY: THE PAYBOOK (static 2026-09-12). Past the rank fields, the 0x0176
#: body is what the counter's Paybook screen lists (0x611925C0; headers
#: 11:20..24 Date / Name / H$ / MP / Paybook): +0x34 u32 row count (<= 20),
#: +0x38 rows of 0x14 = {u8 kind, s8 sub, u16 pad, u32 date (a time_t --
#: polcore's decompose [0x613AE380]+0xAD0, rendered local), s32 H$, s32 MP,
#: u32 pad}. Row text (0x61192316): kind 0 = a DAY HEADER (the date, then
#: '     %s'); kind 1..14 = a line item labelled by 0x61191420 -- 1 Base pay,
#: 2 Kill bonus, 3 Mission participation bonus, 4 Pay cut, 5 Mission bonus,
#: 6 Mission cancellation, 7 City control adjustment (%+2d%% from `sub`),
#: 8 Promotion bonus, 9 Platoon bonus, 10 Platoon bonus refund, 11 Sortie
#: cost refund, 12 Key mission bonus, 13 Arena reward, 14 Arena hosting
#: cancellation (systext group 11). The list appends its own TOTAL row from
#: body+0x00 (H$) and body+0x14 (MP) (0x611926C8), and 0x611754D0 formats
#: body+0x10 as "H$ %s" into lobby+0x7B7C. State 3 of the machine
#: (0x61192AEB) scans all 20 slots for kind == 1, so unused slots stay 0.
#: The rows sort by date (0x61191500 mode 0). WARNING: NOT CONFIRMED IN A LIVE SESSION: the screen
#: has never drawn a row from this server.
S176_TOTAL_MONEY = 0x00
S176_TOTAL_MP = 0x14
S176_ROW_COUNT = 0x34
S176_ROWS = 0x38
S176_ROW_LEN = 0x14
S176_ROW_MAX = 20
PAY_HEADER, PAY_BASE = 0, 1
PAY_KINDS = {1: "Base pay", 2: "Kill bonus", 3: "Mission participation bonus",
             4: "Pay cut", 5: "Mission bonus", 6: "Mission cancellation",
             7: "City control adjustment", 8: "Promotion bonus",
             9: "Platoon bonus", 10: "Platoon bonus refund",
             11: "Sortie cost refund", 12: "Key mission bonus",
             13: "Arena reward", 14: "Arena hosting cancellation"}
DAY = 86400

# --------------------------------------------------------------------------- #
# THE CEASEFIRE BONUS and THE OFFICER REVIEW (2026-09-27). SE's RULES, OUR
# NUMBERS: the archived pages give the mechanisms and almost no figures
# (SE's guide pages: phase, topics20060406,
# update/050719qk2ld8). Every amount, period and count below is ours and is
# named as ours in the knob comments.
# --------------------------------------------------------------------------- #
#: SE (phase:48-53): at each phase end First Sergeant and above get a rank-
#: based ceasefire bonus whatever the result -- First Sergeant..Captain H$ +
#: contribution, Major..Colonel H$ + MP, below First Sergeant nothing. Both
#: sides are paid the SAME amount unless the economic-city points differ by
#: 6:4 or more, then each side's share follows the ratio (topics20060406:50-52).
#: FMO_CEASEFIRE=1 pays it at the Personnel Officer after a judged phase, as a
#: paybook "City control adjustment" line (kind 7: SE scales it by the city
#: points) plus the contribution banked. A pilot's first check only records
#: the phases already judged, so nobody is back-paid for wars they missed.
CEASEFIRE = (os.environ.get("FMO_CEASEFIRE", "").strip() or "0") != "0"
#: OURS: H$ = this many days of the rank's base pay (D15.DAT's pay column).
CEASEFIRE_DAYS = _env_int("FMO_CEASEFIRE_DAYS", "5")
#: OURS: contribution (First Sergeant..Captain) = this % of the rank's bar.
CEASEFIRE_CONTRIB_PCT = _env_int("FMO_CEASEFIRE_CONTRIB_PCT", "2")
#: OURS: MP (Major..Colonel) = this many times the rank's MP pay.
CEASEFIRE_MP_DAYS = _env_int("FMO_CEASEFIRE_MP_DAYS", "5")
RANK_FIRST_SERGEANT, RANK_CAPTAIN, RANK_MAJOR, RANK_COLONEL = 10, 20, 21, 23
PAY_CITY = 7                   #: paybook kind 7 = "City control adjustment"


def ceasefire_share(rec, nation):
    """The multiplier for `nation`'s pilots from one judged phase record
    {ocu, usn}: 1.0 below a 6:4 split, else own share / 0.5. Pure."""
    ocu, usn = int(rec.get("ocu") or 0), int(rec.get("usn") or 0)
    tot = ocu + usn
    if tot <= 0 or max(ocu, usn) / tot < 0.6:
        return 1.0
    own = ocu if nation == 1 else usn if nation == 2 else tot / 2
    return own / tot / 0.5


def ceasefire_bonus(rank, nation, rec, ladder=None):
    """(H$, contribution, MP) one pilot is owed for one judged phase, or None
    below First Sergeant / above Colonel. Pure."""
    rank = int(rank or 0)
    if not RANK_FIRST_SERGEANT <= rank <= RANK_COLONEL:
        return None
    m = ceasefire_share(rec, nation)
    pay, mp = ranks.rank_pay(rank)
    hs = int(round(pay * CEASEFIRE_DAYS * m))
    if rank <= RANK_CAPTAIN:
        bar = ranks.rank_threshold(rank, ladder) or 0
        return hs, int(round(max(0, bar) * CEASEFIRE_CONTRIB_PCT / 100 * m)), 0
    return hs, 0, int(round(mp * CEASEFIRE_MP_DAYS * m))


def ceasefire_owed(char, phases):
    """[(phase number, record)] judged phases this pilot has not been paid.
    MUTATES char["ceasefire_paid"]: the first call only records what is
    already judged (returns []), so the bonus starts with the NEXT phase."""
    judged = sorted((int(k), v) for k, v in (phases or {}).items())
    paid = char.get("ceasefire_paid")
    # Phase numbers start again when the war is restarted (fmowar
    # FMO_WAR_RESTART): each judged record names its war (phase 1's start),
    # and a list paid under another war is for other phases 1, 2, ... A
    # pilot who had a list then is owed every phase of the new war.
    wars = {str(v.get("war")) for _n, v in judged if isinstance(v, dict) and v.get("war")}
    war = max(wars) if wars else None
    if war is not None:
        if isinstance(paid, list) and char.get("ceasefire_war") not in (None, war):
            paid = char["ceasefire_paid"] = []
        char["ceasefire_war"] = war
    if not isinstance(paid, list):
        char["ceasefire_paid"] = [n for n, _r in judged]
        return []
    return [(n, r) for n, r in judged if n not in paid]


#: SE (update 050719qk2ld8:58-71): contribution promotes only up to Captain.
#: Above it a periodic review decides: Captain KEEPS the rank with one
#: SECTOR-mission success in the period, is PROMOTED to Major with "the
#: prescribed count or more", and with none is DEMOTED to First Lieutenant
#: with the contribution bar at about 90%; Major and above the same on AREA
#: missions. Every rank has a headcount limit and a promotion needs a free
#: slot. FMO_REVIEW: 0 (default) off; 'promote' = keep/promote only (no
#: demotion); 'full' = SE's rule with demotion. WARNING: 'full' DEMOTES seeded
#: pilots who never ran a mission -- arm it deliberately.
REVIEW = (os.environ.get("FMO_REVIEW", "").strip() or "0").lower()
REVIEW_DAYS = _env_int("FMO_REVIEW_DAYS", "7")           #: OURS: SE says only "a set period"
REVIEW_PROMOTE = _env_int("FMO_REVIEW_PROMOTE", "3")     #: OURS: SE's count is not published
#: OURS: "rank:cap,..." pilots allowed per rank and nation. SE: 「各階級にはそれぞれ
#: 人数制限が設けられています。昇格するには、各階級の人数枠に空きがある必要があります」
#: (update 050719qk2ld8:71) and never published a figure, so the default is
#: ours, sized for a small server: Major 30, Lieutenant Colonel 15, Colonel 8
#: per nation (a pending promotion order holds its slot). Empty / unset = that
#: default; 'none' (or '0') = no cap at all.
REVIEW_CAPS_DEFAULT = "21:30,22:15,23:8"
REVIEW_CAPS_SPEC = os.environ.get("FMO_REVIEW_CAPS", "").strip()


def parse_review_caps(spec):
    out = {}
    if (spec or "").strip().lower() in ("none", "0", "off"):
        return out
    for piece in (spec or REVIEW_CAPS_DEFAULT).split(","):
        if piece.strip():
            r, c = piece.split(":")
            out[int(r, 0)] = int(c, 0)
    return out


try:
    REVIEW_CAPS = parse_review_caps(REVIEW_CAPS_SPEC)
except ValueError:
    REVIEW_CAPS = {}


def _iso_unix(s):
    import calendar
    try:
        return calendar.timegm(time.strptime(str(s), "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError):
        return None


def review_successes(char, rank, since, now):
    """Completed missions that count for this rank's review in [since, now]:
    SECTOR (category 2) for Captain, AREA (category 3) for Major+."""
    cat = 2 if int(rank) <= RANK_CAPTAIN else 3
    n = 0
    for m in (char or {}).get("missions") or ():
        if not isinstance(m, dict) or m.get("status") != "complete":
            continue
        if int(m.get("cat") or 0) != cat:
            continue
        t = _iso_unix(m.get("reported"))
        if t is not None and since <= t <= now:
            n += 1
    return n


def review_verdict(rank, successes, mode, promote_n=None, slot_free=True):
    """'promote' / 'keep' / 'demote' / None (not reviewed). Pure."""
    rank = int(rank)
    if mode not in ("promote", "full", "1") or not RANK_CAPTAIN <= rank <= RANK_COLONEL:
        return None
    need = REVIEW_PROMOTE if promote_n is None else promote_n
    if successes >= need and rank < RANK_COLONEL and slot_free:
        return "promote"
    if successes >= 1 or mode == "promote":
        return "keep"
    return "demote"


def review_demoted_contribution(new_rank, ladder=None):
    """SE: a demoted officer's contribution bar sits at about 90% toward the
    rank they lost, so contribution alone cannot promote them straight back."""
    lo = ranks.rank_threshold(new_rank, ladder) or 0
    hi = ranks.rank_threshold(new_rank + 1, ladder)
    if hi is None or hi <= lo:
        return lo
    return int(lo + 0.9 * (hi - lo))


# --------------------------------------------------------------------------- #
# THE PERSONNEL OFFICER'S ORDERS (2026-10-07). Promotion / demotion ORDERS
# (辞令) and the promotion OUTLOOK line, SE's flow on the client's own gate.
# --------------------------------------------------------------------------- #
#: KEY: HOW AN ORDER MOVES (static, fmodis 2026-10-07; nothing here is
#: seen live yet). The client holds two rank bytes: A = the rank (lobby+0x8BB,
#: 0x014A +0x2F and 0x0176 +0x0D) and C = the ORDERED rank (lobby+0xE1A,
#: 0x014A +0x58E only: no other writer in the image). 0x61175510 calls an
#: order pending when they differ (see ranks.orders_pending for the floor
#: byte), and then the mission desk's E316 prints D64 67/68/69 "Orders have
#: come down from Army Command / Please ask the Personnel Officer / You
#: cannot accept a mission at this time". The Personnel Officer's 0x0176
#: writes A (+0x0D) and nothing else of the pair -- so the officer is where
#: an order is APPLIED: A := C clears the gate. Hence:
#:   * an order is decided at the officer (the review, a contribution step),
#:     stored on the pilot as `rank_order`, and NOT shown to this session:
#:     its C came from this login's 0x014A, and a +0x0D that differs from it
#:     would lock the desk until the next login;
#:   * the next 0x014A serves A = the held rank, C = the order
#:     (status.rank_and_ack), so the desk sends the pilot to the officer;
#:   * the officer's next 0x0176 carries +0x0D = C and the server banks it.
#: A step the gate does not cover (ranks.orders_pending false for the new
#: rank against this session's C) is applied on the spot instead. With the
#: floor at 0 (what the wire carries today) that never happens, so a
#: CONTRIBUTION step is banked at once and simply shown from the next login
#: (A = C = the new rank, no order), and only REVIEW verdicts become orders --
#: SE's split, since SE's floor (Major) exempted contribution ranks.
#: The pilot's record: `rank_order` = {"rank", "kind" promote|demote,
#: "why" review|contribution, "from", "at"}; `review_last` = the last verdict.
#:
#: THE OUTLOOK LINE (+0x0F, systext group 36, printed to chat and parked in
#: lobby+0x7C for the officer's E315). READ STATICALLY: which index prints
#: which text. GUESSED: which standing SE paired with each index -- SE wrote
#: three families of four and never said when each was used. Ours:
#:   1  "highly regarded, expect greater efforts"  on track (kept / half way)
#:   2  "orders are expected to come down soon"    a promotion is decided
#:   3  "not recognised, a review is unavoidable"  heading for demotion
#:   4  "no message from the top brass"            nothing to report
#:  11  "... expect further results"               review held: KEPT
#:  12  "... the top brass are most satisfied"     promotion order DELIVERED
#:  13  "not recognised (review family)"           demotion order DELIVERED
#:  14  "a promotion review was held but passed over this time"
#:                                                 earned it, rank FULL (cap)
#:  21  "... cannot hide their astonishment"       promoted straight after a demotion
#:  22  "very high, the demotion review has been dropped"
#:                                                 after a demotion: on track now
#:  23  "still not recognised, a demotion review will be held shortly"
#:                                                 a demotion order is decided /
#:                                                 0 wins again after a demotion
#:  24  "reassessed, the demotion review has been dropped"
#:                                                 after a demotion: review KEPT
OUTLOOK_REGARDED, OUTLOOK_ORDERS_SOON, OUTLOOK_UNRECOGNISED, OUTLOOK_NO_MESSAGE = 1, 2, 3, 4
OUTLOOK_REVIEW_KEPT, OUTLOOK_PROMOTED, OUTLOOK_DEMOTED, OUTLOOK_PASSED_OVER = 11, 12, 13, 14
OUTLOOK_ASTONISHED, OUTLOOK_WATCH_DROPPED, OUTLOOK_DEMOTION_SOON, OUTLOOK_REASSESSED = 21, 22, 23, 24
OUTLOOK_TEXT = {
    1: "highly regarded", 2: "orders expected soon", 3: "not recognised (review unavoidable)",
    4: "no message", 11: "review: kept", 12: "promotion delivered", 13: "demotion delivered",
    14: "promotion passed over (rank full)", 21: "astonishment", 22: "demotion review dropped",
    23: "demotion review soon", 24: "reassessed, demotion review dropped"}


def rank_order(char):
    """The pilot's pending order {"rank", "kind", ...} or None."""
    o = (char or {}).get("rank_order")
    if isinstance(o, dict) and isinstance(o.get("rank"), int):
        return o
    return None


def ordered_rank(char, rank):
    """C for this pilot's 0x014A: the pending order's rank, else `rank`
    (no order = acknowledged, the gate open)."""
    o = rank_order(char)
    return o["rank"] if o is not None else int(rank)


def review_mode(mode=None):
    m = (REVIEW if mode is None else str(mode)).strip().lower()
    return "promote" if m == "1" else m


def contribution_outlook(rank, contrib, ladder=None):
    """Outlook below the review band: where the contribution stands against
    this rank's bar and the next one."""
    lo = ranks.rank_threshold(rank, ladder)
    hi = ranks.rank_threshold(rank + 1, ladder) if rank < ranks.RANK_MAX_EARNABLE else None
    if lo is None:
        return OUTLOOK_NO_MESSAGE
    if contrib < lo:
        return OUTLOOK_UNRECOGNISED
    if hi is None or hi <= lo:
        return OUTLOOK_NO_MESSAGE
    if contrib >= hi:
        return OUTLOOK_ORDERS_SOON
    return OUTLOOK_REGARDED if (contrib - lo) * 2 >= (hi - lo) else OUTLOOK_NO_MESSAGE


def personnel_visit(char, client_rank, client_ack, now=None, mode=None, floor=None,
                    held=None, ladder=None, promote_n=None, days=None):
    """One talk with the Personnel Officer: deliver a pending order, run the
    review, take a contribution step, and choose the outlook line.

    `client_rank` / `client_ack` = the A / C this session's client holds
    (status.rank_and_ack at the first visit, then what the last 0x0176 set).
    `held(rank, nation)` counts pilots holding (or ordered to) a rank for the
    cap; None = no cap check. MUTATES `char` (the caller commits when
    `changed`). Returns {"send_rank", "outlook", "changed", "delivered",
    "issued", "verdict", "notes"}."""
    now = int(_now_unix(now))
    mode = review_mode(mode)
    floor = ranks.RANK_ORDER_FLOOR if floor is None else int(floor)
    need = REVIEW_PROMOTE if promote_n is None else int(promote_n)
    period = (REVIEW_DAYS if days is None else int(days)) * DAY
    rank = int(economy._econ_value("rank", None, status.START_RANK, "FMO_RANK", char)[0] or 0)
    contrib = int(economy._econ_value("contribution", None, status.STATUS_CONTRIB,
                                      "FMO_STATUS_CONTRIB", char)[0] or 0)
    out = {"send_rank": int(client_rank), "outlook": OUTLOOK_NO_MESSAGE, "changed": False,
           "delivered": None, "issued": None, "verdict": None, "notes": []}
    note = out["notes"].append

    def apply(new, kind):
        char["rank"] = new
        if kind == "demote" and new <= RANK_CAPTAIN:
            # SE: a demoted Captain keeps the bar at ~90% toward the rank lost
            cap = review_demoted_contribution(new, ladder)
            if contrib > cap:
                char["contribution"] = cap
        # a new rank starts a new review period (or leaves the band)
        if RANK_CAPTAIN <= new <= RANK_COLONEL:
            char["review_at"] = now
        else:
            char.pop("review_at", None)
        out["changed"] = True

    def deliverable(new):
        return not ranks.orders_pending(new, floor, client_ack)

    # 1. A pending order: delivered when this session's client was told of it.
    order = rank_order(char)
    if order is not None:
        if deliverable(order["rank"]):
            apply(order["rank"], order.get("kind"))
            char.pop("rank_order", None)
            out.update(send_rank=order["rank"], delivered=order)
            watch = order.get("why") == "review" and char.get("review_last_before") == "demote"
            char.pop("review_last_before", None)
            out["outlook"] = (OUTLOOK_DEMOTED if order.get("kind") == "demote" else
                              OUTLOOK_ASTONISHED if watch else OUTLOOK_PROMOTED)
            note(f"ORDER DELIVERED: {ranks.rank_name(rank)} -> "
                 f"{ranks.rank_name(order['rank'])} ({order.get('why')}, "
                 f"+0x0D = {order['rank']} = the ordered rank, the desk's gate clears)")
        else:
            out["outlook"] = (OUTLOOK_DEMOTION_SOON if order.get("kind") == "demote"
                              else OUTLOOK_ORDERS_SOON)
            note(f"order to {ranks.rank_name(order['rank'])} pending: this session's "
                 f"client was not told of it (ordered rank byte {client_ack}), it comes "
                 f"down at the next login")
        return out

    def issue(new, kind, why):
        o = {"rank": int(new), "kind": kind, "why": why, "from": rank, "at": now}
        if deliverable(new):
            apply(new, kind)
            out.update(send_rank=int(new), delivered=o)
            note(f"{why} {kind} to {ranks.rank_name(new)} applied at once "
                 f"(the client's gate does not cover it)")
            return True
        if why == "contribution":
            # banked now; the next 0x014A serves A = C = it (no order)
            apply(new, kind)
            out["issued"] = o
            note(f"contribution step to {ranks.rank_name(new)} banked; the client "
                 f"shows it from the next login (+0x0D stays {client_rank} now, "
                 f"or the desk would lock)")
            return False
        char["rank_order"] = o
        if char.get("review_last") == "demote":
            char["review_last_before"] = "demote"
        out.update(issued=o, changed=True)
        note(f"ORDER ISSUED: {kind} to {ranks.rank_name(new)} -- served as the "
             f"ordered rank at the next login, applied at this desk after it")
        return False

    # 2. Contribution: below Captain the ladder decides (SE: up to Captain).
    if rank < RANK_CAPTAIN or (rank == RANK_CAPTAIN and mode not in ("promote", "full")):
        earned = ranks.rank_for_contribution(contrib, ladder) if (
            ranks.RANK_LADDER if ladder is None else ladder) else rank
        if earned > rank:
            now_shown = issue(earned, "promote", "contribution")
            out["outlook"] = OUTLOOK_PROMOTED if now_shown else OUTLOOK_ORDERS_SOON
        else:
            out["outlook"] = contribution_outlook(rank, contrib, ladder)
        return out
    # 3. The review band, Captain..Colonel (SE: Captain on sector missions,
    #    Major+ on area missions; a period, a quota, a cap, demotion).
    if mode not in ("promote", "full") or rank > RANK_COLONEL:
        return out                                    # 4: no message
    since = char.get("review_at")
    if not isinstance(since, int):
        char["review_at"] = now
        out["changed"] = True
        note(f"review period opened for {ranks.rank_name(rank)} "
             f"({period // DAY} days, FMO_REVIEW_DAYS)")
        return out
    wins = review_successes(char, rank, since, now)
    watch = char.get("review_last") == "demote"
    nat = zoneentry.nation_for_session(char, status.STATUS_NATION, "FMO_STATUS_NATION")[0]
    cap = REVIEW_CAPS.get(rank + 1)
    slot = True
    if cap is not None and held is not None and rank < RANK_COLONEL and wins >= need:
        slot = held(rank + 1, nat) < cap
    what = ("sector" if rank <= RANK_CAPTAIN else "area") + " mission(s)"
    if now - since < period:
        # mid-period: the outlook only
        if wins >= need and rank < RANK_COLONEL:
            out["outlook"] = OUTLOOK_ORDERS_SOON if slot else OUTLOOK_REGARDED
        elif wins >= 1:
            out["outlook"] = OUTLOOK_WATCH_DROPPED if watch else OUTLOOK_REGARDED
        elif mode == "full":
            out["outlook"] = OUTLOOK_DEMOTION_SOON if watch else OUTLOOK_UNRECOGNISED
        note(f"review: {wins} {what} since the period opened "
             f"({(now - since) // DAY} of {period // DAY} days; promote at {need})")
        return out
    verdict = review_verdict(rank, wins, mode, promote_n=need, slot_free=slot)
    char["review_at"] = now
    out.update(verdict=verdict, changed=True)
    note(f"REVIEW: {ranks.rank_name(rank)}, {wins} {what} in the period "
         f"(promote at {need}{'' if slot else '; ' + ranks.rank_name(rank + 1) + ' is FULL'}) "
         f"-> {verdict.upper()} (FMO_REVIEW={mode})")
    if verdict == "promote":
        issue(rank + 1, "promote", "review")
        out["outlook"] = (OUTLOOK_PROMOTED if out["delivered"] else OUTLOOK_ORDERS_SOON)
    elif verdict == "demote":
        issue(rank - 1, "demote", "review")
        out["outlook"] = (OUTLOOK_DEMOTED if out["delivered"] else OUTLOOK_DEMOTION_SOON)
    elif wins >= need and not slot:
        out["outlook"] = OUTLOOK_PASSED_OVER
    elif wins >= 1:
        out["outlook"] = OUTLOOK_REASSESSED if watch else OUTLOOK_REVIEW_KEPT
    else:
        out["outlook"] = OUTLOOK_NO_MESSAGE            # 'promote' mode: kept, no wins
    char["review_last"] = verdict
    return out


def _now_unix(now=None):
    return (float(now) if now is not None
            else datetime.datetime.now(datetime.timezone.utc).timestamp())


def paydays_owed(char, now=None, max_days=None):
    """(days, [midnight-UTC time_t per payday, oldest first], today).

    SE pays once a day and banks at most five days at the Personnel desk.
    `last_payday` on the record is a DAY NUMBER (days since the epoch, UTC);
    every day after it up to today is owed, capped at FMO_SALARY_MAX_DAYS.
    A pilot with no `last_payday` yet is owed one day -- on the roll since
    creation, first visit pays today."""
    max_days = SALARY_MAX_DAYS if max_days is None else int(max_days)
    today = int(_now_unix(now) // DAY)
    last = (char or {}).get("last_payday")
    days = 1 if last is None else max(0, today - int(last))
    if SALARY_EVERY:
        days = max(1, days)          # FMO_SALARY=every: a row in every reply
    days = max(0, min(days, max_days))
    stamps = [(today - days + 1 + i) * DAY for i in range(days)]
    return days, stamps, today


#: KEY: FMO_CITY_PAY -- ECONOMIC CITIES SCALE THE SALARY (default 1; 0 = base
#: pay only, the old book). SE, in game (AI/F00/D08 126, 128): 「数多くの経済都市を
#: 制圧することにより、軍から支給される給料が増えます」 and 「また、経済都市が敵軍に
#: 制圧されてしまうと、軍から支給される給料が減ってしまいます」; news/frontline/city:
#: 「経済都市を制圧している数が多いほど、自軍全体の給料が増加します」. The one table
#: SE published (topics0906mission, a Second Lieutenant): 「通常の状態」 100% =
#: H$ 47,000, 「後方都市をひとつ制圧」 112% = H$ 52,640, 「ふたつ」 122% = H$ 56,400,
#: and 「敵軍後方の経済都市を自軍領土にすることで、フリーダム近辺の経済都市よりも大きく
#: 給料を上昇させることができます」 -- a rear city is worth more than one near
#: Freedom, which is exactly fmowar.CITIES' points (Freedom 2, rear 4).
#: The paybook has the line for it: kind 7, systext 11:12 「都市制圧補正(%+2d%%)」,
#: the percent in the row's `sub` byte.
#:
#: THE RULE IS OURS, FITTED TO SE'S ROW: SE never published the formula. We
#: take d = (own city points - enemy city points) / 2, so 0 is "the normal
#: state" (an even split, or nobody holding any), and taking one of the
#: enemy's rear (4-point) cities makes d = 4, two make d = 8. The adjustment
#: is PER_POINT*|d| - FALLOFF*d^2 with the sign of d: 3.25*4 - 4/16 = 12 and
#: 3.25*8 - 64/16 = 22 reproduce SE's 112% / 122% exactly (SE's own H$
#: column disagrees for two cities: 56,400 is 120% of 47,000; we fit the
#: percent column, which is what the 11:12 line prints), the falloff is
#: the upkeep SE describes (「数が多ければ維持費もかかる」), and |d| stops at the
#: peak (26 points, +/-42%) so more cities never pay less. A city the enemy
#: takes moves d down, so pay falls below 100%. H$ only: SE's table is the
#: 給料金額, and says nothing of the MP.
#: WARNING: The holders are read from the war state WHEN THE BOOK IS PAID; the
#: war keeps no history, so every owed day is paid at today's percentage.
CITY_PAY = _env_int("FMO_CITY_PAY", 1) != 0
CITY_PAY_PER_POINT = _env_float("FMO_CITY_PAY_PER_POINT", 3.25)   #: ours, to tune
CITY_PAY_FALLOFF = _env_float("FMO_CITY_PAY_FALLOFF", 0.0625)     #: ours, to tune


def city_pay_pct(own, enemy, per_point=None, falloff=None):
    """The salary adjustment in whole percent (+12 = 112%) for a nation holding
    `own` economic-city points against the enemy's `enemy`. Pure."""
    k = CITY_PAY_PER_POINT if per_point is None else float(per_point)
    f = CITY_PAY_FALLOFF if falloff is None else float(falloff)
    d = (int(own) - int(enemy)) / 2.0
    a = abs(d)
    if f > 0:
        a = min(a, k / (2 * f))         # the peak: past it more would pay less
    pct = int(round(k * a - f * a * a))
    return pct if d >= 0 else -pct


def city_pay_for(char):
    """(percent, why) this pilot's salary is adjusted by, from the economic
    cities its nation and the enemy hold in the war state now. (0, why) when
    FMO_CITY_PAY=0, there is no war state, or the pilot has no nation."""
    if not CITY_PAY:
        return 0, "FMO_CITY_PAY=0"
    if warstate.WAR == "0":
        return 0, "no war (FMO_WAR=0)"
    war = warstate.war_state()
    if war is None:
        return 0, "no war state"
    nat = zoneentry.nation_for_session(char or {}, status.STATUS_NATION,
                                       "FMO_STATUS_NATION")[0]
    if nat not in (1, 2):
        return 0, f"nation {nat!r} holds no cities"
    pts = war.score()
    own, enemy = pts.get(nat, 0), pts.get(3 - nat, 0)
    pct = city_pay_pct(own, enemy)
    return pct, (f"nation {nat} holds {own} economic-city point(s) against "
                 f"{enemy} -> {pct:+d}% (FMO_CITY_PAY)")


def paybook_rows(char, rank, now=None, city_pct=None):
    """([(kind, sub, time_t, H$, MP)], today): a day header + a Base pay line
    per payday owed, paid at the rank's own figures (fmo-ranks.tsv pay/mp),
    and -- when the nation's economic cities move it (FMO_CITY_PAY) -- a
    kind-7 "City control adjustment (%+2d%%)" line per day for the H$
    difference. `city_pct` overrides the war-state reading (tests)."""
    days, stamps, today = paydays_owed(char, now)
    pay, mp = ranks.rank_pay(rank)
    pct = city_pay_for(char)[0] if city_pct is None and stamps else int(city_pct or 0)
    pct = max(-99, min(99, pct))         # `sub` is an s8 drawn as %+2d
    rows = []
    for st in stamps:
        rows.append((PAY_HEADER, 0, st, 0, 0))
        rows.append((PAY_BASE, 0, st, pay, mp))
        if pct and pay:
            rows.append((PAY_CITY, pct, st, int(round(pay * pct / 100.0)), 0))
    return rows, today


def paybook_fill(blk, rows):
    """Write paybook rows and their totals into a 0x0176 body.
    Returns (body, total H$, total MP); rows past 20 are dropped."""
    blk = bytearray(blk)
    rows = list(rows)[:S176_ROW_MAX]
    tm = sum(int(r[3]) for r in rows)
    tmp = sum(int(r[4]) for r in rows)
    struct.pack_into("<I", blk, S176_ROW_COUNT, len(rows))
    for i, (kind, sub, stamp, money, mp) in enumerate(rows):
        struct.pack_into("<BbHIiiI", blk, S176_ROWS + i * S176_ROW_LEN,
                         kind & 0xFF, int(sub), 0, int(stamp) & 0xFFFFFFFF,
                         int(money), int(mp), 0)
    struct.pack_into("<i", blk, S176_TOTAL_MONEY, tm)
    struct.pack_into("<i", blk, S176_TOTAL_MP, tmp)
    return bytes(blk), tm, tmp


#: KEY: 0x0175 -> 0x0176: the service-record / promotion exchange the counter
#: operators run (see the handler). The reply block is the `rep movsd 0x77` at
#: 0x6119291C = 476 B from packet+0x34 = payload+0x20.
MSG_0175_REQ = 0x0175
MSG_0176_REPLY = 0x0176


S176_BODY_LEN = 0x77 * 4               # 476
ANSWER_0175 = os.environ.get("FMO_ANSWER_0175", "1").strip() or "1"
#: KEY: SALARY rides the same exchange (2026-09-12, static). SE: 「給与は1日に1度支給
#: され、最大5日分まで人事課に貯めておくことができます」 (guide/addmanual:107) and
#: 「階級が上がると、支給される給与の額が増える」 (intro/flow3:23). The 0x0176
#: body IS the paybook the counter shows (systext group 11: Base pay / Kill
#: bonus / ... / Total; the graceful arm's own string is 11:8 "Pay and rank
#: processing failed"). FMO_SALARY=0 keeps the rank half and pays nothing.
#:
#: KEY: `every` = PAY ON EVERY CHECK, not once a day. A probe, and the only way
#: to open the CITY CONTROL screen on demand: the machine's state 3
#: (0x61192AEB) scans the 20 paybook slots at block+0x38 for a row whose kind
#: byte is 1 (Base pay) and ONLY on a hit allocates 0x1F0 and calls the City
#: Control ctor (0x61192B46 -> 0x610E86E0); with no Base pay row it falls
#: straight through to the Personal Ratings screen. So once the day's pay is
#: taken, that NPC can never show City Control again until tomorrow -- which
#: is exactly what was hit live on 2026-09-12. `every` keeps a row in
#: every reply (and keeps paying for it, so the screen stays truthful).
SALARY_RAW = os.environ.get("FMO_SALARY", "").strip() or "1"
SALARY = SALARY_RAW != "0"
SALARY_EVERY = SALARY_RAW == "every"
SALARY_MAX_DAYS = _env_int("FMO_SALARY_MAX_DAYS", "5")


# Called at run time only; imported last so that import cycles resolve.
from . import economy, ranks, status, warstate, zoneentry  # noqa: E402
