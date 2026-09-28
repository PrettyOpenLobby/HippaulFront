#!/usr/bin/env python3
"""The Front Mission Online title plugin: the Viewer's profile out of the pilot database.

    python tools/fmo_title_test.py

Needs the OpenLobby core checked out beside this repository (or OPENLOBBY_DIR
pointing at it) for `titles.py` and polcore. Runs on a throwaway database
(OpenLobby's tools/pgtest.py) and SKIPs without one.

Each check is a regression that has already happened once: a store read as
JSON after the data moved into sqlite, the gender byte read as the nation
(the swapped key), a MapKind outside the client's own bands served as a zone.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPENLOBBY = os.environ.get("OPENLOBBY_DIR", os.path.join(ROOT, os.pardir, "openlobby"))
sys.path.insert(0, os.path.join(OPENLOBBY, "services"))
sys.path.insert(0, os.path.join(ROOT, "services"))

import titles          # noqa: E402
import fmodb           # noqa: E402
import fmostore        # noqa: E402
import fmotitle        # noqa: E402

MEMBER = 7
CID = 30000004
FAILS = []


def check(ok, label, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  --  {detail}" if detail else ""))
    if not ok:
        FAILS.append(label)


def main():
    t = fmotitle.register()
    check(titles.for_code(4) is t, "registers as content code 4")
    check(titles.profile_fields(4, CID, MEMBER) == {}, "no pilots: nothing, not a guess")

    fmostore.save_roster(f"member:{MEMBER}", [
        {"id": 1, "first": "Roy", "last": "Bagman", "nation_byte": 1, "nation": 0,
         "mapkind": 205},
    ])
    f = titles.profile_fields(4, CID, MEMBER)
    check(f.get(fmotitle.SLOT_NAME) == "Roy" and f.get(fmotitle.SLOT_FIRSTNAME) == "Roy",
          "the first name on the slot the screen reads (9) and the schema's own (4)", repr(f))
    check(f.get(fmotitle.SLOT_LASTNAME) == "Bagman", "last name on slot 5")
    check(f.get(fmotitle.SLOT_COUNTRY) == 1,
          "nation from nation_byte (creation +0x28), not the `nation` key (the gender)")
    check(f.get(fmotitle.SLOT_ZONE) == 2, "zone = mapkind // 100 inside the client's bands")

    fmostore.save_roster(f"member:{MEMBER}", [
        {"id": 1, "first": "Roy", "last": "Bagman", "nation_byte": 2, "mapkind": 999},
    ])
    f = titles.profile_fields(4, CID, MEMBER)
    check(fmotitle.SLOT_ZONE not in f and f.get(fmotitle.SLOT_COUNTRY) == 2,
          "a MapKind outside every band: zone UNSET, not a made-up number")

    # a pilot written before the nation_byte key existed carries the raw
    # creation record; +0x28 is the nation there
    raw = bytes(0x28) + b"\x02" + bytes(8)
    fmostore.save_roster(f"member:{MEMBER}", [
        {"id": 1, "first": "Old", "last": "Pilot", "raw": raw.hex()},
    ])
    f = titles.profile_fields(4, CID, MEMBER)
    check(f.get(fmotitle.SLOT_COUNTRY) == 2, "nation read out of the raw record when the key is absent")
    check(titles.profile_fields(4, CID, None) == {}, "no member id: nothing")

    print()
    if FAILS:
        print(f"FAILED: {len(FAILS)}: " + ", ".join(FAILS))
        return 1
    print("all Front Mission Online title checks passed")
    return 0


def run():
    with fmodb.test_database() as url:
        if url is None:
            print("SKIP -- no test database (Docker, or POL_TEST_DATABASE_URL)")
            return 0
        return main()


if __name__ == "__main__":
    sys.exit(run())
