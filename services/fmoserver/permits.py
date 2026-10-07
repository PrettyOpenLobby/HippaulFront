"""Area passes and permits: which zones a pilot may open, and at what cost."""
import os
from .knobs import _env_int
from .wirelog import log


#: KEY: SE'S SECTOR TRANSIT PASSES -- item kind 0x13, ids from the client's own
#: master table (grep Pass in the decoded master table): 25 `Pass: HQ-O.C.U.`,
#: 26 `Pass: HQ-U.S.N.`, 27/28 the OC pair, 29 FLZ, 30 DMZ, 31 X, 32 XX.
#: The Personnel Officer's script ANNOUNCES the HQ pass -- AH/F98/D64 records
#: 215/216 (the nation, picked by E060) then 217 "was granted as a reward",
#: gated on its own var 0x8CC -- and SE's server sent the item. We said
#: nothing, so no pilot has ever held one. SE's item text: a 区間移動許可証 is
#: CONSUMED to open a yellow area in the area selector, so this gates travel.
PASS_KIND = 0x13
PASS_HQ = {1: 25, 2: 26}
#: KEY: pass item id -> the ZONE KIND it opens, and the byte the Change Area
#: predicate reads for it: `byte[lobby+0x8C8 + 0x280 + (kind-1)]` must be > 0
#: (0x611A3B30, via 0x611794A0, via the filler 0x61010C2B). Zone kinds are the
#: MapKind hundreds: 1 O.C.U. HQ, 2 O.C.U. occupied, 3 U.S.N. HQ, 4 U.S.N.
#: occupied, 5 frontline, 6 Coliseum -- which is exactly how the passes are
#: named. 30 `Pass: DMZ`, 31 `Pass: X` and 32 `Pass: XX` are deliberately NOT
#: here: no zone kind is proved for them and a guess would open the wrong map.
PASS_ZONE_KIND = {25: 1, 26: 3, 27: 2, 28: 4, 29: 5}
AREA_PERMIT_OFF = 0x280
#: KEY: THE AREA UNLOCK BITMAP -- what a USED travel permit leaves behind
#: (static 2026-09-12). The gate 0x611794A0 ends in
#: `0x611A3950(owned, kind 1, row)`, and 0x611A37A0's jump table puts flag
#: kind 1 at owned+0x00: 0x10 bytes, BIT mode, one bit per ROW of the 0x016C
#: table we served (the row index 0x611A3AA0 hands back, not the zone id).
#: Bit set = the list stores 0 for that zone = "move freely" (0x6101112E
#: `cmp [edi],0 / jle` skips straight to the move); bit clear = 1 = the 18:71
#: "Use one Travel Permit?" prompt. SE's prompt says "once used, you can move
#: as often as you like" -- that promise IS this bit. Its one setter in the
#: image (0x611A39F0, called at 0x61178865) sits in the shop poller's 0x016B
#: arm, so SE's server set it; we never did, which is why a used permit was
#: forgotten. Kept per pilot as the zone ids (`areas_open`) and turned into
#: bits against whatever table is served, so reordering FMO_ZONE_CONTROL
#: cannot open the wrong row.
AREA_OPEN_OFF = 0x00
AREA_OPEN_LEN = 0x10


#: KEY: THE CLIENT'S AREA ACCESS MATRIX, transcribed from 0x613966E4 (static
#: 2026-09-12). 0x611A3B00(row byte, tier) reads
#: dword[(tier*10 + row byte)*4 + 0x613966E4] for tier and row byte in 1..10
#: (anything else = 0): 0 = grey, 1 = needs a permit (unless the area's
#: kind-1 bit is set), 2 = move freely. The ROW BYTE is our 0x016C entry's
#: byte for the pilot's nation (0x611A3AA0: +2 O.C.U., +3 U.S.N.); the TIER is
#: NOT the level itself: see AREA_TIER_BANDS below. With row byte 1 (the
#: `all` table) tiers 1-2 pay a permit and tier 3+ travels free -- the client
#: decides, we only mirror it.
AREA_ACCESS = (
    (1, 0, 0, 0, 0, 0, 0, 0, 0, 0),
    (1, 1, 0, 0, 0, 0, 0, 0, 0, 0),
    (2, 2, 1, 0, 0, 0, 0, 0, 0, 0),
    (2, 2, 1, 2, 0, 0, 0, 0, 0, 0),
    (2, 2, 2, 2, 2, 0, 0, 0, 0, 0),
    (2, 2, 2, 2, 2, 2, 0, 0, 0, 0),
    (2, 2, 2, 2, 2, 2, 0, 0, 0, 0),
    (2, 2, 2, 2, 2, 2, 0, 0, 0, 0),
    (2, 2, 2, 2, 2, 2, 0, 0, 0, 0),
    (2, 2, 2, 2, 2, 2, 0, 0, 0, 0),
)
AREA_PASS_TIER_CLASS = 12       # the class-table kind 0x611782B0 looks for

#: KEY: THE TIER IS A PER-LEVEL BYTE, NOT THE LEVEL (static 2026-09-30). The
#: gate 0x611794A0 calls 0x611782B0 (the class-12 LEVEL, through the exp
#: curve 0x611E40A0) and hands that level to 0x611E4000, which returns
#: `movsx byte[[0x613CA3E8+0x1C] + level*4 - 1]`: byte 3 of row level-1 in the
#: 100 x 4-byte table the D15.DAT parser 0x611E4270 puts after the exp curve
#: (file +0x4960, the "%di" tail fmoclass.py used to call unread). Only THAT
#: byte reaches 0x611A3B00. Read off the shipped file it is a step function:
#:     level 1-4 -> 1, 5-9 -> 2, 10-14 -> 3, 15-19 -> 4, 20-50 -> 5, 51-100 -> 6
#: which is the manual's own ladder (p.37: Occupied Zone from Pilot level 10,
#: the enemy's Occupied Zones from 15, Fierce Battle Zone from 20). Until this
#: the server used the raw level as the tier, so every pilot above level 10
#: fell off the matrix ("grey") and was never charged a pass. Kept as the
#: band starts (level, tier) so a different client build is one line.
AREA_TIER_BANDS = ((1, 1), (5, 2), (10, 3), (15, 4), (20, 5), (51, 6))


def area_tier(level):
    """The 0x611E4000 tier for a class-12 level (AREA_TIER_BANDS). A level
    below 1 reads as 1, as 0x611E4000's own `jge` clamp does."""
    lv = max(1, int(level))
    tier = AREA_TIER_BANDS[0][1]
    for start, t in AREA_TIER_BANDS:
        if lv >= start:
            tier = t
    return tier


def pilot_area_tier(char):
    """(tier, level) of a stored pilot: the class-12 level through
    area_tier()."""
    lv = classes.class_level(classes.class_exp_of(char).get(AREA_PASS_TIER_CLASS, 0))
    return area_tier(lv), lv


def area_access(tier, row_byte):
    """0 grey / 1 permit / 2 free -- 0x611A3B00 over AREA_ACCESS."""
    if not (1 <= int(tier) <= 10 and 1 <= int(row_byte) <= 10):
        return 0
    return AREA_ACCESS[int(tier) - 1][int(row_byte) - 1]


def area_permit_cost(zone, nation, tier, rows):
    """(0 grey | 1 permit | 2 free, why) for a zone the pilot has NOT opened,
    as the Change Area gate 0x611794A0 computes it from the served table."""
    row = next((r for r in rows if int(r[0]) == int(zone)), None)
    if row is None:
        return 0, "zone %d is not in the served 0x016C table" % int(zone)
    rb = int(row[1]) if nation == 1 else int(row[2]) if nation == 2 else -1
    if rb <= 0:
        return 0, "row byte %d for nation %s" % (rb, nation)
    v = area_access(tier, rb)
    return v, "tier %d x row byte %d -> %d" % (int(tier), rb, v)


def area_pass_item(char, zone):
    """The stored transit pass that opens this zone's kind, or None."""
    kind = int(zone) // 100
    for it in inventory.stored_items(char):
        if it["kind"] == PASS_KIND and PASS_ZONE_KIND.get(it["id"]) == kind:
            return it
    return None


def area_open_bitmap(zones, rows):
    """owned+0x00: bit i set when row i of the served 0x016C table is a zone
    the pilot has opened. Zones missing from the table are skipped."""
    b = bytearray(AREA_OPEN_LEN)
    ids = [int(r[0]) for r in rows]
    for z in zones:
        if int(z) in ids:
            i = ids.index(int(z))
            if i < AREA_OPEN_LEN * 8:
                b[i >> 3] |= 1 << (i & 7)
    return bytes(b)
#: FMO_PERMIT: '1' = mint the starter HQ pass, ONCE per pilot, when the
#: Personnel Officer runs his service-record check (0x0175) -- the same talk
#: whose script says he is granting it. Default OFF: the mint push has never
#: been on a wire, and an unproven push is how this server has killed the
#: client before.
PERMIT_RAW = os.environ.get("FMO_PERMIT", "").strip() or "0"
PERMIT = PERMIT_RAW != "0"
#: 'all' = mint every MAPPED pass (one per zone kind 1..5), not just the
#: nation's HQ one. A test lever: it opens every area the pilot's nation is
#: allowed at all, so whatever stays shut is a DIFFERENT gate than the permit.
PERMIT_ALL = PERMIT_RAW == "all"
#: KEY: FMO_PERMIT_RANKS -- passes handed out BY RANK (static 2026-09-12, NOT
#: LIVE). SE's own words (polnews/news4567.shtml:85): a 移動許可証 is "bought in
#: the hangar OR obtained by promotion to a certain rank", and the 2005-06-28
#: patch priced the HQ pass at 1000 H$ and the occupation-zone pass at 2000 H$
#: in the HQ / occupation-zone HANGARS. That sale is NOT in this client's item
#: shop: the kind-0x13 list builder 0x61031A90 drops any row whose D97 price
#: (file 4, +0x00) is 0 -- 0x6102FCB0 returns that price and 0x61031B1D skips
#: zero -- and every pass (25..32), every Rebirth (9..24) and every material
#: (33..40) is priced 0 with restriction digit 9 at +0x08 ("only if the server
#: lists it": 0x6102FCB0 %10 = a nation or 9 -> 0x611757F0 walks the (kind, id)
#: list at lobby+0x8505). And their record byte +1 -- the level 0x61031AEB
#: compares the class-12 level against -- is 60. So the only in-client grant
#: is the 0x016B mint, which is what the officer's promotion script backs for
#: the HQ pass (D64 215/216 + 217) and what 8:2 "Obtained %s" announces for any
#: other. This knob is the "certain rank" half: "<rank>:<pass>,..." where pass
#: is hq / oc / flz (the pilot's nation picks 25/26, 27/28, 29) or an item id
#: 25..29; minted on the Personnel Officer's check once the pilot's rank (after
#: this visit's promotion) is >= the threshold and the pass has never been
#: granted to this pilot (GRANTED_KEY below).
#: Rank bytes are 0-BASED D15 rows: 0 Conscript, 4 Private First Class,
#: 6 Corporal, 10 First Sergeant, 15 Warrant Officer, 18 Second Lieutenant,
#: 21 Major. SE's own threshold for the HQ pass IS in the retail manual
#: (p.44, About Travel Tickets): "when you are promoted to Private First
#: Class (上等兵) you receive one travel permit for the Control District", so
#: the release value is 4:hq. Default unset = nothing beyond FMO_PERMIT.
#: Example: FMO_PERMIT_RANKS=0:oc gives every pilot their nation's OC pass on
#: the first officer visit, which is how an O.C.U. pilot reaches OC-Area 08
#: (207) without the FMO_PERMIT=all test lever.
PERMIT_RANKS_RAW = os.environ.get("FMO_PERMIT_RANKS", "").strip()
PASS_KEYS = {"hq": {1: 25, 2: 26}, "oc": {1: 27, 2: 28}, "flz": {1: 29, 2: 29}}
#: The pilot record's list of pass ids already minted for it. A pass is a
#: one-off grant: SE's manual gives ONE HQ pass at the promotion, and the
#: client consumes it when it opens an area. Until 2026-09-30 grant_hq_pass
#: re-minted any pass the pilot no longer HELD, so every Personnel visit
#: after spending one refilled it.
GRANTED_KEY = "passes_granted"


def granted_passes(char):
    """The pass ids this pilot has already been given: the stored list, plus
    any pass it holds now (a pilot from before the list existed has been
    given at least those)."""
    out = set()
    for v in (char or {}).get(GRANTED_KEY) or ():
        try:
            out.add(int(v))
        except (TypeError, ValueError):
            continue
    for it in inventory.stored_items(char or {}):
        if int(it["kind"]) == PASS_KIND:
            out.add(int(it["id"]))
    return out


def parse_permit_ranks(spec):
    """'<rank>:<pass>,...' -> [(rank, key)], key a PASS_KEYS name or an int id
    in PASS_ZONE_KIND. A bad clause is dropped loudly, never guessed."""
    out = []
    for clause in (spec or "").split(","):
        clause = clause.strip()
        if not clause:
            continue
        try:
            rk, key = clause.split(":", 1)
            rank = int(rk, 0)
            key = key.strip().lower()
            if key not in PASS_KEYS:
                key = int(key, 0)
                if key not in PASS_ZONE_KIND:
                    raise ValueError("no zone kind is proved for that id")
            out.append((rank, key))
        except ValueError as e:
            log(f"[fmo] WARNING: FMO_PERMIT_RANKS: ignoring {clause!r} ({e}; want "
                f"<rank>:<hq|oc|flz|25..29>)")
    return out


PERMIT_RANKS = parse_permit_ranks(PERMIT_RANKS_RAW)


def permit_rank_passes(nation, rank, grants=None):
    """The pass ids FMO_PERMIT_RANKS says a pilot of `nation` at `rank` has
    earned, in threshold order, de-duplicated."""
    out = []
    for thr, key in (PERMIT_RANKS if grants is None else grants):
        if rank is None or int(rank) < int(thr):
            continue
        pid = PASS_KEYS[key].get(int(nation or 0)) if key in PASS_KEYS else key
        if pid is not None and pid not in out:
            out.append(int(pid))
    return out


#: KEY: THE HANGAR MECHANIC SELLS PASSES -- script server call EVENT 206
#: (static 2026-10-02, AI/F00/D93 = scp 0x8073, hanger_event). His menu's
#: permit list (D94 msg 99: 統制区 / 占領区 区間移動許可証の発行) calls the
#: routine at file 0xCFAC (CALL 0x800CE70 + code base 0x13C, from 0xD42C)
#: with the picked ROW in var 0x700 (0xD2E6 / 0xD30E): 0 = Control District
#: (HQ), 1 = Occupied Zone. It submits event 206 with that row in p1 (E220
#: @0xCFC6; LIVE 2026-10-02: p1=0 on the first row, p2 = stack garbage) and
#: reads p2 back out of the answered record (0xCFF6 -> var 0x6D8): p2 == 1 prints
#: D94 102 "This is your transit permit", anything else D94 103 所持金が足り
#: ません "not enough money". So the plain ack this event got until today
#: (p2 echoed as 0) told every pilot they were broke. Nothing in the script
#: charges or grants: SE's server did both, and so do we. (The 2/3 the same
#: list passes to 0xD074 only picks the row's description text.)
HANGAR_SALE_EVENT = 206
HANGAR_SALE_CHOICE = {0: "hq", 1: "oc"}
#: SE's prices (the 2005-06-28 patch, see FMO_PERMIT_RANKS above): HQ pass
#: 1000 H$, occupation-zone pass 2000 H$. Overridable per server.
HANGAR_SALE_PRICE = {"hq": _env_int("FMO_PERMIT_PRICE_HQ", "1000"),
                     "oc": _env_int("FMO_PERMIT_PRICE_OC", "2000")}


def hangar_sale(choice, nation, money):
    """The mechanic's verdict on a pass purchase, as a dict: ok, pass_id,
    price, key and a `why` for the log. Refuses (ok False) an unknown menu
    choice, a nation with no pass of that kind, or a wallet short of the
    price -- the script has one refusal line and it says "not enough money",
    so the log has to carry the real reason."""
    key = HANGAR_SALE_CHOICE.get(int(choice))
    if key is None:
        return {"ok": False, "pass_id": None, "price": 0, "key": None,
                "why": "menu row %d is not a pass row (0 hq / 1 oc)" % int(choice)}
    pid = PASS_KEYS[key].get(int(nation or 0))
    price = int(HANGAR_SALE_PRICE[key])
    if pid is None:
        return {"ok": False, "pass_id": None, "price": price, "key": key,
                "why": "nation %s has no %s pass" % (nation, key)}
    if int(money) < price:
        return {"ok": False, "pass_id": pid, "price": price, "key": key,
                "why": "holds %d H$, the %s pass costs %d H$" % (int(money), key, price)}
    return {"ok": True, "pass_id": pid, "price": price, "key": key,
            "why": "%s pass id %d for %d H$ (held %d)" % (key, pid, price, int(money))}


# Called at run time only; imported last so that import cycles resolve.
from . import classes, inventory  # noqa: E402
