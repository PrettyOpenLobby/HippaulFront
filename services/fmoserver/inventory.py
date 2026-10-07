"""Inventory and wanzer setups (0x0132 -> 0x0133, 0x0165 -> 0x0166): item records and starter
setups."""
import os
import struct
import time
from .wirelog import log


#: 0x0132 -> 0x0133. Success arm 0x6117B700 (je on match, normal polarity):
#:     esi = payload + 8
#:     ecx = 0x960 / rep movsd     ; 9600 bytes -> ebp+0x10D9
#:     [ebp+0x10D5] = [payload]    ; payload +0x00, a u32
#: so the payload is 8 + 9600 = 9608 and the packet 9628. Error arm uses string
#: 0xC0080017.
#:
#: VERIFIED: IT IS THE INVENTORY (2026-08-18, static RE). Decoded from the client's
#: own accessors, and every claim below carries the address it was read at:
#:
#:   0x61177B30  append-an-entry. Ceiling `cmp eax, 0x190 / jl` = 400 entries,
#:               and the element address is `lea edx,[eax+eax*2]` /
#:               `lea eax,[edi+edx*8+0x10D9]` = base + i*24. 400 x 24 = 9600
#:               EXACTLY, so the u32 at +0x00 counts 24-byte entries.
#:               Its overflow print is 0x6133B804, cp932 for "[Debug] item
#:               acquisition error", and its success print 0x6133B7EC,
#:               "[Debug] acquired %s" -- SE naming the list for us.
#:   0x61177BE0  find-by-serial. Compares entry +0x00 and +0x04 against a
#:               64-bit argument pair; the not-found print is 0x6133B880,
#:               "the modified item's SERIAL NUMBER was not found". It then
#:               tests +0x12 against an argument and prints 0x6133B838,
#:               "item already used (%d)" -- so +0x12 is a state byte.
#:   0x61175840  (kind, id) -> a display name. `kind` indexes a jump table at
#:               0x611758C4 (byte map at 0x611758E0, range 0x11..0xD2) that
#:               picks a master table (0x613C14CC, 0x613C14A0, 0x613C120C,
#:               0x613C0F48, 0x613C0FA0, or 0x611A3FE0(kind >> 4)); then
#:               0x611A45E0(table, id) returns the string.
#:   0x61177ED0  a FILTERED iterator over the list (cursor at ebp+0x365D,
#:               limit ebp+0x10D5). Its filters compare the kind byte against
#:               0x11, 0x21, 0x31, 0x41 and 0x13, and one tests
#:               `kind & 0x0F == 2` -- the HIGH nibble is the category and the
#:               low nibble the sub-kind, which is also how 0x61175840 splits it.
#:
#: The 24-byte entry, then:
#:     +0x00  u32   serial low     one 64-bit item SERIAL; the find at
#:     +0x04  u32   serial high    0x61177BE0 matches on both dwords together
#:     +0x08  u16   item id        (into the master table `kind` selects)
#:     +0x0A  u8    item kind      (high nibble = category)
#:     +0x12  u8    state / "already used"
#: WARNING: +0x0B, +0x0C..+0x11 and +0x13..+0x17 have NO identified reader. They are
#: reached, if at all, through a pointer the entry was loaded into, which a
#: displacement scan cannot see -- absence of hits is not absence of a field.
#:
#: Zeros stay the default, but they are now a MEANINGFUL value rather than a
#: probe: count 0 is an empty inventory, a state the client has code for.
MSG_0132_REQ = 0x0132
MSG_0132_REPLY = 0x0133
REPLY_0133_LEN = 8 + 0x960 * 4         # 9608
INV_MAX = 0x190                        # 400, the ceiling at 0x61177B3B
INV_ENTRY_LEN = 0x18                   # 24, from `lea eax,[edi+edx*8+0x10D9]`

#: 0x0165 -> 0x0166. The client sends 0x165 with a 16-byte payload (0x6117B753:
#: init(0x165, 0x10, 0)) and state 7 polls for 0x166 (0x6117B7AF, je on match).
#: Success arm 0x6117B85A: esi = rx+0x14, ecx = 0x440, rep movsd -> ebp+0x3DF3.
#:
#: VERIFIED: IT IS THE WANZER SETUP LIST, 8 x 544 (2026-08-18, static RE). The
#: "8 records of 544" that used to be a guess is measured three independent ways:
#:   0x61174860  a nested search: outer `cmp ebx, 8` with `add edx, 0x220`,
#:               inner `add ecx, 0x18` over entry+0x28.
#:   0x611747D0  is-empty(i): `imul eax, eax, 0x220`, then a byte at entry+0x01.
#:   .rdata      "Setup1".."Setup8" at 0x6133FC08..0x6133FBCF -- EIGHT, named.
#: And the consumer itself prints 0x6133BA34 "Setup empty (SetupID=%d)" at
#: 0x6117B899 when the selected entry's +0x01 is zero, which is what ties that
#: string to THIS message rather than to some other screen.
#:
#: The 544-byte entry:
#:     +0x01  u8    IN USE. 0 = "Setup empty"; 0x611747D0 is the predicate.
#:     +0x02  u8    get 0x61174820 / set 0x61174840
#:     +0x03  u8    get 0x61177DF0 (hands back a pointer, not a value)
#:     +0x0C  u16   get/set 0x61174970 / 0x61174950     four adjacent u16s,
#:     +0x0E  u16   get/set 0x611749B0 / 0x61174990     each with its own
#:     +0x10  u16   get/set 0x611749F0 / 0x611749D0     accessor pair
#:     +0x12  u16   get/set 0x61174A30 / 0x61174A10
#:     +0x16  u8    get/set pair at 0x61174A50 / 0x61174A70
#:     +0x17  u8    get/set pair at 0x61174A90 / 0x61174AB0
#:     +0x28  21 x 24  the EQUIPPED ITEMS -- the SAME 24-byte record as the
#:                     inventory (0x0133), found by the same 64-bit serial.
#:                     0x28 + 21*24 = 0x220 EXACTLY, which is what fixes 21.
#: WARNING: 0x61174860's inner loop runs `cmp eax, 0x15 / jle`, i.e. TWENTY-TWO
#: iterations, so its last read lands 24 bytes past the entry, in the next
#: setup's header. The tiling says 21; the loop bound is SE's off-by-one. Do
#: not "correct" the layout to match the loop.
#:
#: WARNING: THE BYTE AT ebp+0x3DF2 IS NOT A COUNT AND IS NOT OURS. 0x6117A782 writes
#: it from a UI setter, and every accessor uses it as `(it - 1) * 0x220` -- it
#: is the SELECTED setup, 1-based, client-side state. The consumer reads it only
#: to decide whether to print "Setup empty". That closes the open question in
#: the earlier note about where the count comes from: nowhere, it is not a count.
#:
#: WARNING: AND THE PAYLOAD IS 4357, NOT 4352. After the rep movsd the same arm
#: does `mov eax,[esp+0x14]` / `add eax, 0x1100` at 0x6117B8A5 -- [esp+0x14] is
#: the payload pointer saved at 0x6117B86E, and esp is balanced on both paths --
#: then reads a u32 at payload+0x1100 into ebp+0x3669 and a byte at
#: payload+0x1104 into ebp+0x366D. A 4352-byte reply leaves the client reading
#: five bytes of whatever its receive buffer still held. 4357 is what it asks
#: for; the extra five stay zero, so this changes what we SEND, not what we mean.
#: KEY: WHY THERE IS A BUILDER HERE NOW (2026-08-23). This message was answered
#: with `bytes(REPLY_0166_LEN)` -- 4,357 zeros -- so every setup's IN-USE byte
#: was 0 and the client sat in its own "Setup empty (SetupID=%d)" state. Live
#: that shows up as a pilot whose NINE part slots are attached and whose nine
#: part-model objects are all blank: `kind(+0x0A)=0`, and the render node keeps
#: its constructor AABB `(-5,-1,-5)/(5,5,5)`.
#:
#: The part-model object at a part record's +0x08 IS a 24-byte ITEM RECORD --
#: the same record the inventory (0x0133) and this setup's equipped list use.
#: Measured: ours read back `serial=1, id=0, kind=0`, which is an empty item.
#: So the starter wanzer was never client-side data to be recovered; SE's
#: server AUTHORED it here, which is why the local saves held no loadout and
#: why 0x013E carries a CLASS rather than part ids.
#:
#: WARNING: WHAT IS STILL DATA, NOT CODE. `id` indexes a master table that `kind`
#: selects, and those tables are filled at load from the data tree -- so this
#: file must NOT mint ids. A wrong id resolves to a missing model; 0x611F5700
#: skips a zero slot SILENTLY, so a partial setup degrades rather than crashes.
#:
#: KEY: AND THE IDS DID NOT HAVE TO BE MINTED -- SE PUT THEM IN THE CLIENT
#: (2026-08-23). See STARTER_SETUPS below. The equipped-item records are read
#: back by the WORLD unit's builder at 0x61002FEB, which is the same loop, with
#: the same slot->item map, as the CHARACTER-CREATION preview at 0x61012ACF --
#: and the preview's parts come from six 48-byte blobs addressed by
#: `0x611E1F90(nation, class)`. Those previews demonstrably DRAW. So the
#: starter setup is a TRANSPOSITION of client data, not a design decision.
#:
#: The valid `kind` values are the client's own, read off the jump-table map at
#: 0x611758E0 (`lea ecx,[eax-0x11] / cmp ecx,0xC1 / ja default`, then
#: `movzx ecx, byte [ecx+0x611758E0] / jmp [ecx*4+0x611758C4]`): 0x11, 0x13,
#: 0x21, 0x31, 0x41, and every kind whose LOW NIBBLE IS 2 (0x12, 0x22 .. 0xD2).
#: Anything else lands on the default arm and has no master table at all.
SETUP_IN_USE = 0x01                    # 0 = "Setup empty" (0x611747D0)
ITEM_SERIAL_LO, ITEM_SERIAL_HI = 0x00, 0x04
ITEM_ID, ITEM_KIND = 0x08, 0x0A

#: KEY: HOW THE 21 EQUIPPED ITEMS BECOME THE PILOT'S 12 RENDER PARTS. The world
#: builder 0x61002FEB walks the slot list at 0x61385760 and, for each, maps the
#: slot to an ITEM INDEX through 0x6138A2A0 (via the one-line getter
#: 0x6102D6E0), reads `id` and `kind` out of `setup + 0x28 + idx*24`, skips a
#: zero id, and calls `0x611F5700(slot, (id << 16) | kind, 1, &item[+0x0C])`.
#:     0x61385760 = [0, 1, 2, 3, 4, 5, 6, 7, 10]   -- NINE slots
#:     0x6138A2A0 = [1, 0, 3, 2, 5, 4, 7, 6, 4, 4, 10]
#: Those nine are exactly the "9 of 12 part slots populated (indices 0-7 and
#: 10)" that `fmocrash.py --wanzer` measured live, which is what identifies
#: 0x61002FEB as the consumer of THIS message rather than one of the other 38
#: callers of 0x611F5700.
#:
#: WARNING: ITEMS 8, 9 AND 11-20 ARE NEVER READ by this builder (8 and 9 map to item 4
#: alongside part slot 5, and are not in the slot list at all). Filling them
#: changes nothing on screen, so "N items equipped" is not "N parts drawn".
SLOT_TO_ITEM = [1, 0, 3, 2, 5, 4, 7, 6, 4, 4, 10]
WORLD_PART_SLOTS = [0, 1, 2, 3, 4, 5, 6, 7, 10]
VALID_ITEM_KINDS = frozenset(
    [0x11, 0x13, 0x21, 0x31, 0x41] + [(n << 4) | 2 for n in range(1, 14)])


#: KEY: SE'S OWN STARTER WANZERS, (nation, class) -> [(item index, kind, id)].
#:
#: WHERE THESE COME FROM, exactly. `0x611E1F90(nation, class)` is a bare address
#: computation -- `nation 1 -> 0x613996D0, nation 2 -> 0x61399760`, then
#: `+ (class - 1) * 0x30`, and 0 for anything outside nation 1..2 / class 1..3.
#: Each 0x30 blob is `u32 class` then eleven `{u8 kind, u8 pad, u16 id}` entries
#: indexed by SETUP ITEM INDEX. The creation-preview builder 0x61012ACF walks
#: part slots 0..10, maps each through `0x6138A2A0` (= the same
#: `[1,0,3,2,5,4,7,6,4,4,10]` the world unit uses), skips a zero kind and calls
#: `0x611F5700(slot, (id << 16) | kind, 1, ...)`.
#:
#: The WORLD unit's builder at 0x61002FEB is the identical loop -- same slot
#: list (`0x61385760` = `[0,1,2,3,4,5,6,7,10]`, which is exactly the "9 of 12
#: part slots populated" the live dump measured), same index map -- except that
#: it reads `kind`/`id` out of THIS message's equipped-item records
#: (`setup + 0x28 + idx*24`, `id @+0x08`, `kind @+0x0A`) instead of out of the
#: blob. So serving the blob's contents as item records is not a guess about
#: what SE's server did; it is the same data on the other side of the wire.
#:
#: WARNING: WHY THIS IS NOT "MINTING AN ID". Every id here was read out of
#: FrontMissionOnline.dll at a fixed address, cross-checked against the master
#: tables it indexes (an offline decoder of
#: `Data\AG\F21\D97.DAT`), and each one resolves to a NAMED part -- the
#: names in the comments below are the client's own strings, not labels anyone
#: invented. `fmoitems.py selftest` re-reads the blobs from the image and fails
#: if this transcription drifts; run it before changing a number here.
#:
#: WARNING: THE INDEX IS PART OF THE DATUM. The backpack lives at item 10, not at
#: "the next free slot" -- item 10 is what part slot 10 reads. A dense list
#: would put it at item 5 and quietly equip it as something else.
STARTER_SETUPS = {
    # OCU
    (1, 1): [(0, 0x11, 1), (1, 0x21, 1), (2, 0x31, 1), (3, 0x31, 1),
             (4, 0x12, 1), (10, 0x41, 1)],           # Arco / 22snLeosocial / BP-C01
    (1, 2): [(0, 0x11, 21), (1, 0x21, 21), (2, 0x31, 21), (3, 0x31, 21),
             (7, 0x72, 1), (10, 0x41, 1)],           # Wildgoat / Donkey / BP-C01
    (1, 3): [(0, 0x11, 26), (1, 0x21, 26), (2, 0x31, 26), (3, 0x31, 26),
             (4, 0x12, 49), (10, 0x41, 141)],        # Giza / Cemetery / RP2A1-Chord
    # USN
    (2, 1): [(0, 0x11, 51), (1, 0x21, 51), (2, 0x31, 51), (3, 0x31, 51),
             (4, 0x12, 1), (10, 0x41, 1)],           # Husky Mk.III
    (2, 2): [(0, 0x11, 61), (1, 0x21, 61), (2, 0x31, 61), (3, 0x31, 61),
             (7, 0x72, 1), (10, 0x41, 1)],           # Valiant / Donkey
    (2, 3): [(0, 0x11, 66), (1, 0x21, 66), (2, 0x31, 66), (3, 0x31, 66),
             (4, 0x12, 49), (10, 0x41, 141)],        # Crevette / Cemetery
}
STARTER_NAMES = {
    (1, 1): "Arco", (1, 2): "Wildgoat", (1, 3): "Giza",
    (2, 1): "Husky Mk.III", (2, 2): "Valiant", (2, 3): "Crevette",
}

#: The pair used when the character we are serving has no nation/class on file
#: -- a synthesised roster carries neither. 0x611E1F90 returns NULL outside
#: nation 1..2 / class 1..3, so there is no "neutral" value to pass through:
#: picking one is unavoidable and is therefore logged every time.
STARTER_FALLBACK = (1, 1)

#: `FMO_SETUP_STARTER=0` restores the all-zero block byte for byte. It exists
#: because this file's own ledger says a behavioural default is a claim, and a
#: claim needs a way to be falsified in one restart.
SETUP_STARTER = os.environ.get("FMO_SETUP_STARTER", "1") not in ("0", "")

#: PROBE: HOW MANY of the eight setups to fill. Default 1, which is what SE would
#: have served -- a new pilot has one loadout.
#:
#: WARNING: WHY THIS IS A KNOB AND NOT A CONSTANT (2026-08-23). Live, the setup
#: arrives INTACT at `lobby+0x3DF3` (setup 1, IN USE=1, all six items with the
#: right ids) and the pilot's nine part records STILL read `+0x10 = 0` -- the
#: field `0x611F5700` writes `(id << 16) | kind` into. So the dresser ran and
#: equipped nothing. A scene rebuild via Move, with the setup already in
#: memory, changed nothing either, which RULES OUT the timing/race reading.
#:
#: The remaining static candidate is the INDEX. `0x61003120` walks the 0x0153
#: unit-id array (`lobby+0x6B96+ebx*4`) for `ebx` in 0..7, skips ids the entity
#: map does not have, and dresses each unit it creates from setup **ebx+1**. We
#: serve one unit id at array slot 0, which should mean setup 1 -- but that
#: chain is read, not measured. `FMO_SETUP_FILL=8` fills all eight, so the
#: client finds the wanzer at WHATEVER index it reads. It cannot tell us which
#: index, but it settles whether the index is the variable at all -- and a
#: player having eight identical loadouts is a normal state, not a hack.
#: (`fmocrash.py --setup` now prints the unit-id array beside the setups, which
#: answers "which index" directly the next time a session is live.)
SETUP_FILL = max(0, min(int(os.environ.get("FMO_SETUP_FILL", "1") or 1), 8))


def starter_setup(nation, cls):
    """(nation, class) -> the item list, or [] for a pair the client's own
    0x611E1F90 would reject. Never substitutes a neighbour: an unknown class is
    not a class 1 pilot, and pretending otherwise is how a wrong model gets
    served and then believed."""
    return list(STARTER_SETUPS.get((nation, cls), ()))


def parse_setup_parts(spec):
    """`["11:5", "10=41:1"]` -> [(0, 0x11, 5), (10, 0x41, 1)].

    An entry may carry an EXPLICIT item index (`<idx>=<kind>:<id>`); without one
    it takes the next position, starting at 0. Kinds the client has no master
    table for are DROPPED LOUDLY rather than served as a silent dud.

    WARNING: The explicit form is not a convenience. The world builder reads item
    indices 0-7 and 10 only (0x6138A2A0), so a dense list can never place the
    backpack, and an operator writing the parts out in reading order would get
    a setup that equips it into part slot 5's arm mount instead."""
    out, nxt = [], 0
    for e in spec:
        idx, has_idx, rest = e.partition("=")
        if not has_idx:
            idx, rest = None, e
        kind, _, ident = rest.partition(":")
        try:
            k, i = int(kind, 16), int(ident, 0)
            n = nxt if idx is None else int(idx, 0)
        except ValueError:
            log("   FMO_SETUP_PARTS entry %r is not `[idx=]kind:id` -- "
                "dropping it" % (e,))
            continue
        if k not in VALID_ITEM_KINDS:
            log("   FMO_SETUP_PARTS kind %#04x has no master table "
                "(0x611758E0 sends it to the default arm) -- dropping %r"
                % (k, e))
            continue
        if not 0 <= n < SETUP_ITEMS:
            log("   FMO_SETUP_PARTS item index %d is outside 0..%d -- "
                "dropping %r" % (n, SETUP_ITEMS - 1, e))
            continue
        out.append((n, k, i))
        nxt = n + 1
    return out


def item_record(serial, item_id, kind):
    """One 24-byte item record, the shared currency of 0x0133 and 0x0166."""
    b = bytearray(INV_ENTRY_LEN)
    struct.pack_into("<II", b, ITEM_SERIAL_LO, serial & 0xFFFFFFFF,
                     (serial >> 32) & 0xFFFFFFFF)
    struct.pack_into("<H", b, ITEM_ID, item_id & 0xFFFF)
    b[ITEM_KIND] = kind & 0xFF
    return bytes(b)


#: KEY: THE PILOT'S OWN ITEMS (2026-09-11). Until now an ACQUIRE (0x0168 ->
#: 0x016B) minted a record the client appended to ITS list and we forgot; the
#: next 0x0133 served only what the garage block equips, so every bought part
#: vanished at relog -- while the client had ALREADY debited the price from
#: its wallet (0x6117886D..: money -= [pending+4] after a 0x016B) and we had
#: not, so the next 0x014A gave the money back. Two ledgers, neither right.
#: The items a pilot acquired live on the character (`items` in the record's
#: extra JSON: serial, id, kind, price, when); 0x0133 lists them after the
#: setups' own records, a SELL/DISCARD removes them, and the acquire debits
#: the price the client will debit. Serials are the same 64-bit currency the
#: garage block uses (mint_serial), so a bought part can be equipped and then
#: found by 0x61177BE0 at the next login.
def stored_items(char):
    """[{serial, id, kind, ...}] on a character; [] when none."""
    out = []
    for it in (char or {}).get("items") or ():
        try:
            out.append({"serial": int(it["serial"]), "id": int(it["id"]),
                        "kind": int(it["kind"]), **{k: v for k, v in it.items()
                                                    if k not in ("serial", "id", "kind")}})
        except (KeyError, TypeError, ValueError):
            continue
    return out


def stored_item_records(char):
    """The 24-byte records of a pilot's acquired items, for 0x0133."""
    return [item_record(it["serial"], it["id"], it["kind"])
            for it in stored_items(char)]


def add_stored_item(char, serial, item_id, kind, price=0):
    """Append one acquired item to the character. Returns the entry."""
    entry = {"serial": int(serial), "id": int(item_id), "kind": int(kind),
             "price": int(price),
             "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    items = [it for it in (char.get("items") or ()) if isinstance(it, dict)]
    items.append(entry)
    char["items"] = items
    return entry


def remove_stored_item(char, serial):
    """Drop the item with this 64-bit serial. True when one was there."""
    items = [it for it in (char.get("items") or ()) if isinstance(it, dict)]
    keep = [it for it in items if int(it.get("serial", -1)) != int(serial)]
    if len(keep) == len(items):
        return False
    char["items"] = keep
    return True


def merge_inventory(*lists):
    """Records from several sources, first occurrence of a serial wins,
    capped at the client's 400-entry ceiling."""
    seen, out = set(), []
    for lst in lists:
        for rec in lst:
            key = bytes(rec[ITEM_SERIAL_LO:ITEM_SERIAL_LO + 8])
            if key in seen or not any(rec):
                continue
            seen.add(key)
            out.append(bytes(rec))
            if len(out) >= INV_MAX:
                return out
    return out


def setup_entry(parts, in_use=1, serial_base=1):
    """One 544-byte wanzer setup: header, then 21 equipped-item records.

    `parts` is [(item index, kind, id)] -- the index is honoured, so gaps are
    real gaps. Each record gets a distinct non-zero serial: the client finds an
    equipped item by its 64-bit serial (0x61174860's inner search), so two
    records sharing one would be the same item in two places."""
    b = bytearray(SETUP_ENTRY_LEN)
    b[SETUP_IN_USE] = in_use & 0xFF
    for idx, kind, item_id in parts:
        if not 0 <= idx < SETUP_ITEMS:
            continue
        off = SETUP_ITEM_OFF + idx * INV_ENTRY_LEN
        b[off:off + INV_ENTRY_LEN] = item_record(serial_base + idx,
                                                 item_id, kind)
    return bytes(b)


def setup_parts_for(nation=None, cls=None):
    """What setup 1 should carry, and WHY -- returns (parts, source).

    Precedence: an explicit FMO_SETUP_PARTS wins (it is the A/B lever), then
    the starter table for this character's (nation, class), then empty. The
    reason is returned rather than logged here so the caller can put it in the
    one line that also reports what went on the wire."""
    if SETUP_PARTS:
        return parse_setup_parts(SETUP_PARTS), "FMO_SETUP_PARTS"
    if not SETUP_STARTER:
        return [], "FMO_SETUP_STARTER=0 -- the all-zero block, as before"
    if (nation, cls) in STARTER_SETUPS:
        return starter_setup(nation, cls), (
            "SE's starter wanzer for nation %s class %s (%s)"
            % (nation, cls, STARTER_NAMES[(nation, cls)]))
    fb = STARTER_FALLBACK
    return starter_setup(*fb), (
        "nation %r class %r is not a pair 0x611E1F90 accepts (needs nation "
        "1-2, class 1-3) -- falling back to nation %d class %d (%s)"
        % (nation, cls, fb[0], fb[1], STARTER_NAMES[fb]))


def reply_0166(parts=None, slots=None, nation=None, cls=None):
    """The 0x0166 body: 8 x 544 setups, then the u32 + byte tail at +0x1100.

    Setup 1 is marked IN USE whenever parts are configured; the rest stay empty,
    which is a state the client has code for. With no parts the whole block
    stays zero -- byte-identical to what this served before -- so
    FMO_SETUP_STARTER=0 is a real revert, not a different kind of empty.

    WARNING: SETUP 1 IS THE ONE THE WORLD READS. 0x610031AC passes `ebx + 1` to the
    world builder and 0x611748C0 resolves index i to `lobby + 0x3BD3 + i*0x220`
    -- and this payload lands at `lobby + 0x3DF3`, which is index 1. So the
    FIRST 544 bytes of this reply are what the pilot wears."""
    if parts is None:
        parts, _ = setup_parts_for(nation, cls)
    b = bytearray(REPLY_0166_LEN)
    if parts:
        n = SETUP_FILL if slots is None else slots
        n = max(0, min(n, SETUP_SLOTS))
        for i in range(n):
            off = i * SETUP_ENTRY_LEN
            # Distinct serial ranges per setup: the client finds an equipped
            # item by its 64-bit serial (0x61174860), so eight setups sharing
            # one serial space would be eight views of the same item.
            b[off:off + SETUP_ENTRY_LEN] = setup_entry(
                parts, serial_base=1 + i * SETUP_ITEMS)
    return bytes(b)


#: WARNING:KEY: THE INVENTORY IS WHY THE WANZER READS "-Nothing-" IN EVERY SLOT.
#: Decoded 2026-09-08 against a player's own stored garage, which is
#: CORRECT: setup 1 in_use=1 carrying a complete starter Giza (idx0 0x11:26,
#: idx1 0x21:26, idx2/3 0x31:26, idx4 0x12:49, idx10 0x41:141) with distinct
#: serials 1,2,3,4,5,11 -- and the status screen still showed Weight 0,
#: Power 0 and "-Nothing-" in all nine part slots.
#:
#: The setup does not CONTAIN parts, it REFERENCES them: the client resolves
#: each equipped slot by searching the inventory for that entry's 64-bit
#: serial (find-by-serial 0x61177BE0, whose miss prints 0x6133B880, SE's own
#: "the modified item's SERIAL NUMBER was not found"). `0x0132 -> 0x0133`
#: served 9,608 ZEROS -- count 0, an empty inventory -- so every one of those
#: six lookups missed and every slot rendered empty. The client was right and
#: our data was incomplete.
#:
#: KEY: The join is free: an equipped record and an inventory entry are THE SAME
#: 24 BYTES (item_record: "the shared currency of 0x0133 and 0x0166"), so the
#: inventory is not a second thing to author -- it is exactly the set of
#: records the garage block already equips, copied verbatim so the serials
#: cannot drift apart. A serial in a setup that is NOT in the inventory is,
#: by construction, a slot that renders "-Nothing-".
#:
#: WARNING: STATIC INFERENCE, not yet confirmed in a live session. It explains the symptom, the
#: 2026-08-23 measurement ("the setup arrives INTACT ... the pilot's nine part
#: records STILL read +0x10 = 0 -- the dresser ran and equipped nothing") and
#: SE's own not-found string, but no client has seen a non-empty 0x0133.
#: `FMO_INVENTORY=0` restores the all-zero reply byte for byte.
INVENTORY_FROM_SETUPS = os.environ.get("FMO_INVENTORY", "").strip() != "0"


def inventory_from_setups(block):
    """Every distinct item record a setups block equips, as inventory entries.

    Deduplicated by the 64-bit serial (two entries sharing one serial would be
    one item seen twice, which is exactly what 0x61177BE0's search cannot
    disambiguate), and capped at the client's own 400-entry ceiling."""
    seen, out = set(), []
    for si in range(SETUP_SLOTS):
        base = si * SETUP_ENTRY_LEN
        if base + SETUP_ENTRY_LEN > len(block):
            break
        for i in range(SETUP_ITEMS):
            off = base + SETUP_ITEM_OFF + i * INV_ENTRY_LEN
            rec = block[off:off + INV_ENTRY_LEN]
            if len(rec) < INV_ENTRY_LEN or not any(rec):
                continue
            serial = bytes(rec[ITEM_SERIAL_LO:ITEM_SERIAL_LO + 4]
                           + rec[ITEM_SERIAL_HI:ITEM_SERIAL_HI + 4])
            if serial in seen:
                continue
            seen.add(serial)
            out.append(bytes(rec))
            if len(out) >= INV_MAX:
                return out
    return out


def reply_0133(entries=()):
    """The 0x0133 body: u32 count at +0x00, then 400 x 24B records at +0x08.

    The client copies 9,600 bytes from payload+8 to ebp+0x10D9 and takes the
    u32 at payload+0x00 as the entry count (0x6117B700)."""
    b = bytearray(REPLY_0133_LEN)
    n = min(len(entries), INV_MAX)
    struct.pack_into("<I", b, 0, n)
    for i, rec in enumerate(list(entries)[:n]):
        off = 8 + i * INV_ENTRY_LEN
        b[off:off + INV_ENTRY_LEN] = rec
    return bytes(b)


MSG_0165_REQ = 0x0165
MSG_0165_REPLY = 0x0166
SETUP_SLOTS = 8                        # "Setup1".."Setup8"; 0x61174860's `cmp ebx, 8`
SETUP_ENTRY_LEN = 0x220                # 544
SETUP_ITEM_OFF = 0x28                  # where the 21 equipped-item records start
SETUP_ITEMS = 21                       # (0x220 - 0x28) / 24, exactly
SETUP_TAIL_OFF = 0x1100                # 8 * 544 -- the u32 read at 0x6117B8A5
REPLY_0166_LEN = SETUP_TAIL_OFF + 5    # 4357: the block, then a u32 and a byte
#: KEY: THE TAIL IS THE SET JOBS (2026-09-30). lobby+0x3669..+0x366D, five job
#: kinds, slot 0 the MAIN job (script getter 0x610F93DE), 0 = empty. The client
#: sends them in 0x0167 at payload+0x1108 (0x6117857A) = our stored block's
#: +0x1100, and reads them back from 0x0166 +0x1100 (0x6117B8A5). Only the Job
#: List window (0x611AB0D9) and two per-slot setters write them.
SETUP_JOBS_LEN = 5


def set_jobs(char):
    """The pilot's set jobs from its stored garage block, main first: job
    kinds 1..8, empty and repeated slots dropped. [] when nothing is stored."""
    try:
        block = bytes.fromhex((char or {}).get("setups") or "")
    except ValueError:
        return []
    if len(block) < SETUP_TAIL_OFF + SETUP_JOBS_LEN:
        return []
    out = []
    for k in block[SETUP_TAIL_OFF:SETUP_TAIL_OFF + SETUP_JOBS_LEN]:
        if 1 <= k <= 8 and k not in out:
            out.append(k)
    return out

#: `kind:id` pairs for setup 1's equipped list, kind in HEX. EMPTY (the default)
#: serves the all-zero block this message served before -- see reply_0166.
SETUP_PARTS = [e for e in os.environ.get("FMO_SETUP_PARTS", "")
               .replace(" ", "").split(",") if e]
