"""The character-select list (0x012E -> 0x012F): roster slots and the ids the client is served."""
import os
import struct
from .knobs import _env_int


#: After the time sync the client sends 0x12E (empty) and waits for 0x12F. The
#: handler at 0x6117A156 copies a LARGE structure out of the reply:
#:
#:     eax = rx + 0x14          ; payload
#:     esi = payload + 4
#:     edi = ebp + 0x20C
#:     ecx = 0x1A0 / rep movsd  ; 416 dwords = 1664 bytes
#:     [ebp+0x208] = [payload]  ; payload +0x00
#:     [ebp+0x20] = 4           ; the outer mode advances to 4
#:
#: so the payload is u32 + 1664 bytes = 1668, and the packet 1688. What is IN
#: those 1664 bytes is NOT known -- 1664 = 416 dwords, and nothing here says how
#: it is divided. We send zeros as an honest probe and let the client react.
MSG_LIST_REQ = 0x012E
MSG_LIST_REPLY = 0x012F
LIST_REPLY_LEN = 4 + 0x1A0 * 4          # 1668

# THE 0x12F PAYLOAD IS A 32-SLOT LIST -- decoded 2026-08-18 from the consumer at
# 0x61172A80, which is a method of the same class that sends 0x1AB:
#
#     edx = [globals+0x194]        ; the login object
#     eax = [edx+0x208]            ; THE COUNT
#     if (eax <= 0) -> return      ; empty list, nothing to show
#     esi = [edx+ebp+0x20C]        ; entry +0x00 : u32 id
#     lea edi,[edx+ebp+0x210]      ; entry +0x04 : string, to +0x14
#     lea esi,[edx+ebp+0x221]      ; entry +0x15 : string, to +0x25
#     al  = [eax+0x26]             ; entry +0x26 : a byte
#     add ebp, 0x34                ; STRIDE 52 -- and 1664/52 = 32 exactly
#
#     payload +0x00   u32 count
#     payload +0x04   32 x 52-byte entries
#
# WHY IT MATTERS: with count = 0 the loop does nothing, so "Start Game" fails
# INSTANTLY with error [FM00000] and never touches the network -- which is
# exactly what was reported. The error formatter is "[%s%05d] %s" at 0x6133AB64;
# code 0 means no code was set, i.e. a local precondition, not a server failure.
#
# VERIFIED: THE FIELDS ARE NAMED, and not by guessing at the shape of the bytes: the
# CLIENT SENT THIS RECORD BACK TO US. Live 2026-08-20, creating a character
# through the nation screen (0x01AB opens it, 0x01AA submits it), the 0x01AA
# request carried
#
#     0000  01 00 00 00 43 61 73 00 00 ...        id 1, "Fox"   at +0x04
#     0010  00 00 00 00 00 4e 6f 74 65 64 00 ...  "Noted"       at +0x15
#     0020  00 00 00 00 00 00 02 00 ...           2             at +0x26
#
# -- and the player had just typed "Fox" / "Noted" and picked the SECOND nation
# on screen. So the submit payload is a 0x012F entry, field for field:
#
#     +0x00  u32   character id
#     +0x04  17B   FIRST NAME, NUL-terminated
#     +0x15  17B   LAST NAME
#     +0x26  u8    NATION -- 2 = USN (United States of the New Continent).
#                  The screen offers two, OCU first, so 1 is almost certainly
#                  OCU; only 2 has actually been seen.
#     +0x27..+0x33 still unread by any consumer we have looked at.
#
# WARNING: NATION 0, WHICH IS WHAT WE SERVE, IS PROBABLY NOT A REAL NATION -- no screen
# offers it. It is left at 0 anyway: the whole sequence from this list through
# 0x0153 to a live UDP world channel was measured with 0, and changing a field
# that is working to one that is merely more plausible is how a working
# sequence gets broken. FMO_LIST_NATION exists for the A/B.
LIST_ENTRY_LEN = 0x34
LIST_SLOTS = 32
LIST_COUNT = _env_int("FMO_LIST_COUNT", "0", 10)
LIST_NAME = os.environ.get("FMO_LIST_NAME", "TESTPILOT")
#: The LAST name. Defaults to the first, which is what this served before the
#: two fields were told apart -- so the default is byte-identical to what every
#: measurement so far was taken against.
LIST_LAST = os.environ.get("FMO_LIST_LAST", LIST_NAME)
LIST_NATION = _env_int("FMO_LIST_NATION", "0", 10)

#: WARNING: THE SCENE'S SCRIPT RESOURCE IS CHOSEN BY THE NATION BYTE, AND 0 DISABLES
#: THE CHOICE. Read out of the client 2026-08-20; NOT measured on a wire.
#:
#: `0x61005100` is the site that registers the scene's resources, and the id it
#: registers for type 3 -- the SCRIPT -- is not simply the MapKind we send:
#:
#:     if globals[0x1AC] == 1 and not (600 <= MapKind <= 607):
#:         n = byte[lobby + 0x8B4]
#:         script_id = 98 if n == 1 else 99 if n == 2 else MapKind
#:     else:
#:         script_id = MapKind
#:
#: `globals[0x1AC]` is our 0x0153 +0x18, which we send as 1. `0x61004AE0` is the
#: same arithmetic again as a plain accessor, and `0x61016F00` is the one-line
#: getter for the byte. So for a nation of 1 or 2 the client IGNORES the MapKind
#: we send and loads one of two script ids it HARDCODES -- and for nation 0,
#: which is what we serve, the substitution never fires and it loads our MapKind.
#:
#: WARNING: THIS RETIRES THE "MapKind 600 BEHAVES EXACTLY LIKE 100" NEGATIVE AS A
#: NEGATIVE. 600 is precisely the band that BYPASSES the substitution, and at
#: 100 the substitution did not fire either, because the nation was 0. Both runs
#: therefore took the same branch and registered the raw MapKind. The run varied
#: the one input that both branches ignore, which is why it could not tell them
#: apart. **A negative result is only about the path the run actually took.**
#:
#: WARNING: AND THE LAST HOP IS UNPROVEN. Nothing has been found that WRITES
#: `lobby+0x8B4`: a linear sweep of .text over that displacement finds two
#: readers and no writer at all, so it is filled through a pointer the sweep
#: cannot follow. That the byte IS the nation is inferred from its two legal
#: values -- 1 and 2, exactly the nation enum the client sent us in its own
#: 0x01AA (2 = USN) -- not from having watched it be assigned, and not from
#: having traced our 0x012F +0x26 into it. **Serving a nation is a PROBE, not a
#: fix**, and the measurement that would settle it is `lobby+0x8B4` itself,
#: read live the way [fmokey] reads polcore.
#:
#: FMO_NATION forces the nation byte in EVERY 0x012F entry, on both the
#: synthetic and the stored path. 0 = serve each character's own nation, which
#: is byte-for-byte what every measurement so far was taken against.
NATION = _env_int("FMO_NATION", "0", 10)
if NATION:
    print(f"[fmo] WARNING: FMO_NATION={NATION} is INERT since 2026-09-11: it wrote the "
          f"0x012F entry's +0x26, which is the GENDER byte, not the nation. The "
          f"nation is per character (creation +0x28, FMO_NATION_PER_CHARACTER); "
          f"unset this knob.")


def list_entry(entry_id, first, last, nation=0, nation_byte=None):
    """One 52-byte slot: id, first name, last name, gender, nation.

    The layout is the consumer's (0x61172A80 walks it at stride 0x34) and the
    MEANINGS are the producer's -- the 0x01AA the client sends when it creates a
    character is this same record, which is what named the fields.

    WARNING: `nation` here is the record's +0x26, which is the GENDER (the swapped-key
    hazard) -- the parameter kept its old name so every caller still lines up.
    The NATION is +0x28 (`nation_byte`), and until 2026-09-08 it was served as
    ZERO in every slot: the client's own copy of the character said "no
    nation" while 0x014A said O.C.U. or U.S.N. Now it carries the pilot's own
    (character_nation) when known.
    """
    b = bytearray(LIST_ENTRY_LEN)
    struct.pack_into("<I", b, 0x00, entry_id)
    f = first.encode("ascii", "replace")[:0x10]         # 17-byte field, keep a NUL
    l = last.encode("ascii", "replace")[:0x10]
    b[0x04:0x04 + len(f)] = f
    b[0x15:0x15 + len(l)] = l
    #: 2026-09-11: FMO_NATION no longer touches this byte. +0x26 is the GENDER
    #: (see above), so "forcing the nation" here forced the wrong field --
    #: the pilot's model changed and their nation did not. The record's own
    #: value is served; the nation rides at +0x28 below.
    b[0x26] = nation & 0xFF
    if nation_byte in (1, 2):
        b[0x28] = nation_byte
    return bytes(b)


def list_payload(count):
    """u32 count + 32 fixed-size slots, from the synthetic probe entry.

    Only used when the character store is disabled; roster_payload() is the
    real path.
    """
    body = bytearray(LIST_REPLY_LEN)
    struct.pack_into("<I", body, 0, count)
    for i in range(min(count, LIST_SLOTS)):
        off = 4 + i * LIST_ENTRY_LEN
        body[off:off + LIST_ENTRY_LEN] = list_entry(
            i + 1, LIST_NAME, LIST_LAST, LIST_NATION)
    return bytes(body)


#: WARNING: THE ID OF THE SYNTHETIC EMPTY SLOT. See `roster_payload` -- it must be
#: NON-ZERO or the client's own free-slot search rejects it.
FREE_SLOT_ID = 1

#: KEY: THE CHARACTER ID THE CLIENT SEES = the stored id + FMO_CHAR_WIRE_BASE.
#:
#: The client's selected character id becomes its own UnitID ([mgr+0x2C],
#: globals+0x1BC -> the "%xlobby"/"%xbattle" key and the own unit's +0x08), and
#: its unit tick 0x61064720 SKIPS every unit whose UnitID is below 10 before it
#: builds or sends movement state (`cmp [ebp+8],0xA; jb` at 0x6106659C).
#: We served ids 1, 2, ... so a pilot's OWN wanzer
#: never sent cmd 23/24: live 2026-09-27, AI enemies (0x2222..) moved on both
#: screens and the allied pilots never did. Retail ids were large.
#: The store keeps its ids (1, 2, ...); only the wire is shifted, at the two
#: boundaries -- roster_payload() out, Session.find()/apply_charsel() in -- so no
#: stored character is renumbered. 0x1000 clears the room aliases (0x200..),
#: the enemy squad (0x2222..) and the NPC ids (0x8208....). 0 = the old ids.
CHAR_WIRE_BASE = _env_int("FMO_CHAR_WIRE_BASE", "0x1000")


def to_wire(store_id):
    """Stored character id -> the id the client is served."""
    return (store_id + CHAR_WIRE_BASE) if CHAR_WIRE_BASE and store_id else store_id


def from_wire(wire_id):
    """An id the client sent -> the stored id. Idempotent on a stored id (they
    never reach CHAR_WIRE_BASE), so a caller that already converted is safe."""
    if CHAR_WIRE_BASE and wire_id is not None and wire_id >= CHAR_WIRE_BASE:
        return wire_id - CHAR_WIRE_BASE
    return wire_id


def has_named_character(roster):
    """The client's own test, transcribed: a character exists iff some slot
    carries a non-empty NAME (`0x61174EB0` reads `byte [entry+0x04]`)."""
    return any(c.get("first", "") for c in roster)


def with_free_slot(roster):
    """The roster as the CLIENT needs to see it.

    WARNING: AN EMPTY LIST IS NOT "NO CHARACTER" TO FMO -- IT IS "NO SLOTS", AND IT
    SELECTS THE WRONG MENU. The title screen picks between its two menus in
    five instructions at `0x61042CD0`:

        ecx = lobby
        eax = 0x61174E50(lobby)        ; find a slot whose NAME is empty
        cl  = (eax < 0)                ; NOT FOUND -> 1
        menu = descriptors[cl]         ; [0] = 0x6138D0D8  Create Character
                                       ; [1] = 0x6138D068  Start Game / Change
                                       ;                   Nations / Delete

    and `0x61174E50` walks `[lobby+0x208]` entries at `lobby+0x20C` stride 0x34,
    takes the first with `byte [entry+0x04] == 0`, stores that entry's id in
    `lobby+0x7426`, and returns **-8** when it finds none -- which a count of 0
    guarantees, because the loop never runs. Negative -> menu 1. So serving an
    honest empty list showed the account the HAS-CHARACTER menu, and the only
    thing on it resembling creation was "Change Nations".

    That is exactly what the 2026-08-23 02:03Z session did: Change Nations
    (0x01AB) -> nation picker -> name entry -> 0x01AA carrying **id 0**, because
    `lobby+0x7426` had never been set. Measured, not inferred.

    So: when the account has no named character, serve ONE slot with an EMPTY
    name and a NON-ZERO id. `0x61174E50` returns >= 0 only if the id is
    non-zero (`test edx,edx; jne` on `lobby+0x7426`), so id 0 would still fail.

    WARNING: AND ONLY THEN. Appending a free slot to a roster that already has a
    character would make the search succeed at the second slot and flip the
    menu BACK to Create Character, hiding Start Game. The client only ever
    examines slot 0 for "do I have a character" (`0x61174EB0` clamps its own
    loop bound to 1), so one character per account is the client's model too.
    """
    if has_named_character(roster):
        return roster
    if roster:
        # A slot already exists and is unnamed -- that IS the free slot.
        return roster
    return [{"id": FREE_SLOT_ID, "first": "", "last": "", "nation": 0}]


def roster_payload(roster):
    """The 0x012F body for a stored roster.

    WARNING: Only id / first / last / nation are filled. The entry's +0x27..+0x33 are
    served as zeros because no consumer we have read touches them -- the
    appearance bytes we DO have from 0x013E are not put there on a guess, since
    a wrong field in a record the character-select screen renders is exactly the
    kind of change that looks like a client bug later.
    """
    roster = with_free_slot(roster)
    body = bytearray(LIST_REPLY_LEN)
    struct.pack_into("<I", body, 0, min(len(roster), LIST_SLOTS))
    for i, c in enumerate(roster[:LIST_SLOTS]):
        off = 4 + i * LIST_ENTRY_LEN
        nb = popnation.character_nation(c)[0] if zoneentry.NATION_PER_CHARACTER else None
        body[off:off + LIST_ENTRY_LEN] = list_entry(
            to_wire(c["id"]), c.get("first", ""), c.get("last", ""),
            c.get("nation", 0), nation_byte=nb)
    return bytes(body)


# Called at run time only; imported last so that import cycles resolve.
from . import popnation, zoneentry  # noqa: E402
