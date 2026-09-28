"""The Front Mission Online title plugin for the OpenLobby core.

The game runs as its own service (fmo.py). This module is the part of Front
Mission Online that lives INSIDE the core's login process: the content
profile the Viewer shows for a Front Mission Content ID (prof_004.pfb),
built from the pilot database (fmostore.py) the game writes.

Loaded with POL_TITLES=fmotitle in the core's login and authsess services;
see docker-compose.title.yml. The pilots are in the stack's PostgreSQL
database (POL_DATABASE_URL, which those services already have), in the game's
own fmo_ tables; the first read applies HippaulFront's migrations if the game
service has not yet (fmodb.py).
"""
import struct

import titles
import fmostore

#: the N of prof_004.pfb
CONTENT_CODE = 4

#: The profile's slots. The screen's Player Name label reads slot 9 (z_name);
#: slot 4 (z_firstname) is the schema's own first-name slot and gets the same
#: value. Front Mission has no World Name label, so slot 6 stays unset.
SLOT_FIRSTNAME, SLOT_LASTNAME, SLOT_COUNTRY, SLOT_ZONE, SLOT_NAME = 4, 5, 7, 8, 9

#: The zone enum from prof_004.pfb: 1 O.C.U. Headquarters, 2 O.C.U.
#: Occupation Zone, 3 U.S.N. Headquarters, 4 U.S.N. Occupation Zone,
#: 5 Frontline Zone, 6 Coliseum. These are the client's MapKind bands in
#: order, so the zone a pilot stands in is `mapkind // 100`, the client's own
#: arithmetic, not a table of ours.
MAPKIND_BANDS = ((100, 109), (200, 209), (300, 309),
                 (400, 409), (500, 519), (600, 607))
ZONE_MAX = 6


def in_mapkind_band(kind):
    """True if the client has a compare that accepts this MapKind."""
    return any(lo <= kind <= hi for lo, hi in MAPKIND_BANDS)


def creation_field(c, key, off, width=1):
    """One creation-record field for a stored pilot: the named key when the
    record carries it, else read out of `raw`, which every pilot ever made
    carries verbatim, else None."""
    v = c.get(key)
    if v is not None:
        return v
    raw = c.get("raw") or ""
    if len(raw) < (off + width) * 2:
        return None
    try:
        b = bytes.fromhex(raw)
    except ValueError:
        return None
    return b[off] if width == 1 else struct.unpack_from("<H", b, off)[0]


def nation_of(c):
    """The pilot's nation, 1 = O.C.U., 2 = U.S.N., or None: creation +0x28
    (`nation_byte`). NEVER the `nation` key: that is creation +0x26, the
    gender byte, a swapped key that was read by accident once."""
    return creation_field(c, "nation_byte", 0x28)


def profile_of(row):
    """`{slot: value}` for one pilot. Only what the row holds."""
    out = {}
    if row.get("first"):
        out[SLOT_NAME] = out[SLOT_FIRSTNAME] = row["first"]
    if row.get("last"):
        out[SLOT_LASTNAME] = row["last"]
    nation = nation_of(row)
    if nation in (1, 2):
        out[SLOT_COUNTRY] = int(nation)
    kind = row.get("mapkind")
    if kind is not None and in_mapkind_band(int(kind)):
        zone = int(kind) // 100
        if 1 <= zone <= ZONE_MAX:
            out[SLOT_ZONE] = zone
    return out


class FrontMissionOnline(titles.Title):
    tag = b"FM0"
    content_code = CONTENT_CODE

    def describe(self):
        return f"Front Mission pilot database {fmostore.DB_PATH}"

    def profile_fields(self, cid, member_id):
        if member_id is None:
            return {}
        rows = fmostore.load_roster(f"member:{member_id}") or []
        if not rows:
            return {}
        return profile_of(rows[0])


def register():
    return titles.register(FrontMissionOnline())
