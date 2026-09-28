"""The parts array on a battle POP: the wanzer a pilot fitted."""
import os
import struct
from .deps import fmoworld


#: VERIFIED:KEY: FMO_BATTLE_PARTS -- the wanzer's ELEVEN PART RECORDS in the battle
#: self-POP body (fmoworld.POP_PARTS, body+0x8C). Decoded 2026-09-08; this is
#: the answer to the live measurement "0 of 12 part slots populated" in map 418.
#:
#: KEY: THE BATTLE DOES NOT DRESS FROM THE GARAGE. The lobby wanzer is built by
#: 0x61002FEB out of the 0x0166 setup block at lobby+0x3DF3, and that path is
#: LOBBY-ONLY -- its walk 0x61003120 iterates the 0x0153 unit-id array through
#: the lobby entity manager [0x613CA470], which scene 4 does not use. The
#: battle unit is dressed by 0x611F70A0 (called from the wanzer spawn
#: 0x611ED760) out of the POP BODY'S OWN part array. Two dressers, two sources;
#: we were filling one of them.
#:
#: WARNING: THIS ALONE CANNOT DRESS ANYTHING, AND THAT IS THE POINT. 0x611EB2C3 sends
#: UnitType 4 to the human dresser 0x611E7190, which never calls 0x611F70A0. So
#: this needs `FMO_UDP_POP_BATTLE=<uid>:<type>` with a type in 0/1/2/3/5/6 as
#: well. That knob was armed alone on 2026-09-08, fired, and changed nothing --
#: which this decode EXPLAINS rather than contradicts: it made the client reach
#: a dresser whose eleven records were all zero, and 0x611F7172's
#: `test eax,eax / je` skips every zero slot silently. Necessary, not
#: sufficient; the two together are the test.
#:
#: Default ON, because it can only fire on a battle channel popping a non-human
#: UnitType -- a combination that requires FMO_UDP_POP_BATTLE, which is off. It
#: therefore changes NOTHING until an operator arms that knob, and the lobby
#: (whose wanzer already builds) is never touched. `FMO_BATTLE_PARTS=0` reverts.
BATTLE_PARTS = os.environ.get("FMO_BATTLE_PARTS", "1") not in ("0", "")


def stored_setup1_parts(block):
    """[(item index, kind, id)] out of a stored 0x0166 block's SETUP 1.

    Setup 1 is the first 544 bytes (reply_0166: this payload lands at
    lobby+0x3DF3, which the client resolves as index 1). Only the eleven
    indices 0x611F70A0 reads are returned -- items 11..20 exist in the setup
    and have no part record, and silently dropping them here is correct
    because the client drops them too.

    Returns [] for a block that is not in use, so a caller can tell "the player
    has no saved loadout" from "the player saved an empty one"."""
    if len(block) < inventory.SETUP_ENTRY_LEN or not block[inventory.SETUP_IN_USE]:
        return []
    out = []
    for idx in range(fmoworld.POP_PART_COUNT):
        off = inventory.SETUP_ITEM_OFF + idx * inventory.INV_ENTRY_LEN
        item_id = struct.unpack_from("<H", block, off + inventory.ITEM_ID)[0]
        kind = block[off + inventory.ITEM_KIND]
        if item_id and kind:
            out.append((idx, kind, item_id))
    return out


def pop_parts_for(host_ip):
    """(parts, source) for the battle self-POP's part array, or ([], why).

    KEY: SAME SOURCE AS THE GARAGE, on purpose. A player who re-equips in the
    hangar must sortie in what the hangar shows, so this reads the STORED 0x0166
    block first -- the client-authored 0x0167 save, exactly the precedence
    Session.setups_block() uses -- and only synthesises the starter when there
    is none. Deriving the battle loadout independently is how the two would
    drift apart, which is the mistake that left the wanzer
    reading -Nothing- in every slot.

    WARNING: A dropped part is NAMED in the source string, never silent. An empty part
    slot is the exact symptom this whole decode exists to explain; one that
    came from our own validator must not be mistakable for one that came from
    the client."""
    if not BATTLE_PARTS:
        return [], "FMO_BATTLE_PARTS=0"
    acct = identity.account_for(host_ip)
    char = None
    for c in charstore.load_roster(acct):
        char = c
        break
    if char is None:
        return [], "no character on file for %s" % (acct[:8],)
    src = None
    parts = []
    stored = char.get("setups")
    if stored:
        try:
            blk = bytes.fromhex(stored)
        except ValueError:
            blk = b""
        if len(blk) == inventory.REPLY_0166_LEN:
            parts = stored_setup1_parts(blk)
            if parts:
                src = "store:%s saved setup 1 (0x0167)" % (acct[:8],)
    if not parts:
        nat, _natsrc = popnation.character_nation(char)
        parts, why = inventory.setup_parts_for(nat, char.get("cls"))
        src = "store:%s -> %s" % (acct[:8], why)
    # Validate HERE so the reason lands in the log line beside the wire bytes;
    # record_pop would raise and the caller would only learn the POP was
    # refused, not which part was wrong.
    keep, dropped = [], []
    for idx, kind, item_id in parts:
        if idx >= fmoworld.POP_PART_COUNT:
            dropped.append("item %d (%#04x:%d): 0x611F70A0 reads only 0..%d"
                           % (idx, kind, item_id, fmoworld.POP_PART_COUNT - 1))
        elif kind not in fmoworld.POP_PART_KINDS:
            dropped.append("item %d kind %#04x has no master table"
                           % (idx, kind))
        elif not 0 < item_id <= 0xFFFF:
            dropped.append("item %d id %r is not 1..65535" % (idx, item_id))
        else:
            keep.append((idx, kind, item_id))
    if dropped:
        src += " -- DROPPED, not sent: " + "; ".join(dropped)
    return keep, src


# Called at run time only; imported last so that import cycles resolve.
from . import charstore, identity, inventory, popnation  # noqa: E402
