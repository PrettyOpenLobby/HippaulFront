"""Who stands in a Change Room "Room": the room people per ZONE kind, their catalogue
bodies and the shipped default cast.

Pure data plus one registration helper; imports nothing from the package, so
deps.py can hand the catalogue rows to fmoworld before any other module reads
NPC_CATALOGUE (charstore's reserved names among them).

WHY PER ZONE. No script attaches map 121 or 124 on its own: which map a room
is and who stands in it is the server's choice. The room NPC's KEY is looked
up in the ZONE's event table (the bank loads per zone, not per map), so the
person a key speaks as depends on the zone the room hangs off:

    HQ (1xx / 3xx)        0x82081022  Pvt James Douglas (U.S.N.: Louis Klein)
                          0x82081023  Kyoko Harada (HQ only)
    occupied (2xx / 4xx)  0x82081012  Francis Sassoon
    frontline (5xx)       0x82081022  1st Lt Charles Forster (U.S.N.: Ernest Miller)
    every zone            0x82080800  the training sergeant

Positions are the room scripts' own: they stage the player at (4.0, 0, 1.56)
and the NPC at (5.42, 0, 1.56) facing 270 on map 121's open floor; Harada's
mark is (6.58, 0, 11.03) facing 90. Facing is degrees, the layout file's
convention (fmolayout.face_to_wire signs it for the wire). The sergeant keeps
the body and spot prod's layout gave it (Viduka, cat 102).
"""

#: The room people's catalogue bodies, as typecodes 300+. Regenerate, never
#: hand-transcribe: `fmonpccat.py --scp <script> --python` prints each row
#: (columns as fmoworld.NPC_CATALOGUE: name1, name2, selector, face, uniform,
#: size, build, rec+0x40).
ROOM_CATALOGUE = {
    # AI/F00/D99.DAT -- catalogue block at 0x16950, 19 records (fmonpccat.py)
    300: ('James', 'Douglas', 1, 53, 101, 1, 3, 0),      # D99 row 0
    301: ('Louis', 'Klein', 1, 53, 201, 1, 3, 0),        # D99 row 13
    302: ('Kyoko', 'Harada', 2, 59, 103, 1, 1, 0),       # D99 row 10 (= AI/F01/D03 row 0)
    303: ('Mary', 'Burnet', 2, 59, 202, 1, 1, 0),        # D99 row 11 (= AI/F01/D03 row 5)
    # AI/F00/D19.DAT -- catalogue block at 0x1E230, 44 records (fmonpccat.py)
    304: ('Charles', 'Forster', 1, 58, 152, 2, 1, 0),    # D19 row 0
    305: ('Ernest', 'Miller', 1, 58, 252, 2, 1, 0),      # D19 row 4
    # AI/F01/D15.DAT -- catalogue block at 0x16D00, 14 records (fmonpccat.py)
    306: ('Francis', 'Sassoon', 2, 60, 103, 3, 2, 0),    # D15 row 0
    307: ('Elisabeth', 'Wright', 2, 53, 203, 3, 2, 0),   # D15 row 5
}

#: O.C.U. typecode -> the U.S.N. person in its place (npccast.usn_counterpart).
#: Douglas -> Klein and Forster -> Miller are the scripts' own pairs.
#: WARNING: Harada -> Burnet and Sassoon -> Wright are read off the catalogue
#: row order only (D99 and D03 list Harada, Burnet back to back; D15 lists
#: Sassoon, Wright), the same O.C.U.-then-U.S.N. order every proved pair in
#: those blocks follows. No E060 branch was read for them.
ROOM_USN = {300: 301, 302: 303, 304: 305, 306: 307}

ROOM_TALK_SPOT = (5.42, 0.0, 1.56)
SERGEANT_KEY = 0x82080800


def _row(key, cat, pos, face, label=None):
    return {"key": key, "type": 4, "x": pos[0], "y": pos[1], "z": pos[2],
            "face": face, "cat": cat, "label": label}


def _sergeant():
    # prod's layout 'room' band, 2026-10-06: Viduka, face 0
    return _row(SERGEANT_KEY, 102, (-0.46, 0.0, 13.69), 0.0, "Training.Sergeant")


#: band id (fmolayout.ROOM_ZONE_BANDS) -> the default rows, in the layout
#: file's row shape. A layout band of the same id replaces them outright.
ROOM_DEFAULT_ROWS = {
    "room_hq": [
        _row(0x82081022, 300, ROOM_TALK_SPOT, 270.0),
        _row(0x82081023, 302, (6.58, 0.0, 11.03), 90.0),
        _sergeant(),
    ],
    "room_occ": [
        _row(0x82081012, 306, ROOM_TALK_SPOT, 270.0),
        _sergeant(),
    ],
    "room_fz": [
        _row(0x82081022, 304, ROOM_TALK_SPOT, 270.0),
        _sergeant(),
    ],
}


def register(catalogue):
    """Add the room people to `catalogue` (fmoworld.NPC_CATALOGUE) without
    overwriting a row it already has. Returns how many were added."""
    n = 0
    for k, v in ROOM_CATALOGUE.items():
        if k not in catalogue:
            catalogue[k] = v
            n += 1
    return n
