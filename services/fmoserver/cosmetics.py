"""The cosmetics shop screen (0x01A2 -> 0x01A3)."""
import os
import struct
from .knobs import _env_int


#: KEY: `0x01A2 -> 0x01A3` -- **THE COSMETIC CATALOGUE**, and the reason the pilot
#: locker turned the player into a stewardess.
#:
#: Both hangar terminals open by sending 0x01A2, and the one byte at payload+0x00
#: says WHICH catalogue (measured live 2026-09-11, both screens on one session):
#:   byte 1  the PILOT.LOCKER   -> accessories (kind 0) + pilot suits (kind 1)
#:   byte 2  the SETUP.CONSOLE  -> camo (2) + colours (3) + emblems (4)
#: The reply's parse 0x61172950 takes `u32 count` from payload+0x00 and copies
#: 0x3840 bytes from payload+0x44 as up to 1800 entries of
#: `{u16 id, u8 kind, u8 unused, u32 price}` (the six list builders read only
#: +0, +2 and +4; the purchase at 0x611788FB copies the same three).
#:
#: We answered 14,468 ZEROS -- count 0 -- so every picker had no rows, and an
#: outfit change fell back to a default body. Same failure shape as the 09-09
#: insignia crash: an EMPTY list is not a neutral answer.
#:
#: The rows are the client's OWN tables, banked from
#: Data/BB/F13/D27..D31.DAT with SE's English names. `nation_flag` is the
#: record's +0x04: 1 and 2 are the two nations (`Garrison Cap` and `Officer's
#: Dress Hat` each exist twice, once per nation; the splits are 26/27, 88/84,
#: 49/46, 33/33), 0 is nation-neutral, and 255 marks a placeholder record
#: (`HEAD_F91`, `Pilot Suit 41`) which is dropped at extraction. The client
#: applies the same equality test itself for insignia (0x611BE344 against
#: byte[lobby+0x8B4]), so we filter the same way.
#:
#: WARNING: THE PRICE IS OURS AND SE'S IS NOT KNOWN. Unlike the PART tables -- whose
#: fourth file really is 0x1C bytes per id with the buy price at +0x00 -- these
#: five carry no money field at all (+0x28/+0x2c are an index and a model id).
#: FMO_COSMETIC_PRICE is therefore an invention, and it defaults to 0 so that it
#: cannot take money a player should have kept. The only attested cosmetic price
#: anywhere is the 2nd Huffman Conflict medal at H$10 (topics/060428).
A2_PILOT, A2_WANZER = 1, 2
A2_KINDS = {A2_PILOT: (0, 1), A2_WANZER: (2, 3, 4)}
A3_COUNT_OFF = 0x00
A3_ROW_OFF = 0x44                      # the `lea esi,[eax+0x44]` at 0x6117296B
A3_ROW_LEN = 8
A3_ROW_MAX = 0xE10 * 4 // A3_ROW_LEN   # 1,800 -- the rep movsd count
COSMETIC_TSV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "fmodata", "fmo-cosmetics.tsv")
#: 0 = serve the 14,468 zeros we always have (the stewardess state, kept as an
#: A/B); 1 = serve the catalogue.
COSMETIC_SHOP = (os.environ.get("FMO_COSMETIC_SHOP", "").strip() or "1") != "0"
COSMETIC_PRICE = _env_int("FMO_COSMETIC_PRICE", "0")


def load_cosmetics(path=COSMETIC_TSV):
    """{kind: [(id, nation_flag, english)]} from fmo-cosmetics.tsv, or {}."""
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        cols = f.readline().rstrip("\r\n").split("\t")
        for line in f:
            r = dict(zip(cols, line.rstrip("\r\n").split("\t")))
            try:
                out.setdefault(int(r["kind"]), []).append(
                    (int(r["id"]), int(r["nation_flag"]), r.get("english", "")))
            except (KeyError, ValueError):
                continue
    return out


COSMETICS = load_cosmetics()


def cosmetics_for(screen, nation):
    """[(kind, id, english)] this screen offers this nation, in table order.

    A row is offered when its nation flag is 0 (both) or equals the character's
    nation -- the client's own rule for insignia. Capped at A3_ROW_MAX."""
    rows = []
    for kind in A2_KINDS.get(screen, ()):
        for rid, flag, en in COSMETICS.get(kind, ()):
            if flag in (0, nation):
                rows.append((kind, rid, en))
    return rows[:A3_ROW_MAX]


def reply_01a3(need, req, nation):
    """The 0x01A3 body for this 0x01A2 request, or None to fall back to zeros."""
    screen = req[0] if req else 0
    if not COSMETIC_SHOP or screen not in A2_KINDS or not COSMETICS:
        return None
    rows = cosmetics_for(screen, nation)
    if not rows:
        return None
    b = bytearray(need)
    struct.pack_into("<I", b, A3_COUNT_OFF, len(rows))
    for i, (kind, rid, _en) in enumerate(rows):
        struct.pack_into("<HBBI", b, A3_ROW_OFF + i * A3_ROW_LEN,
                         rid & 0xFFFF, kind & 0xFF, 0, COSMETIC_PRICE)
    return bytes(b)
