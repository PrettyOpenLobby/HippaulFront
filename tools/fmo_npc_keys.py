#!/usr/bin/env python3
"""fmo_npc_keys.py -- the entity keys FMO's lobby NPC EVENT TABLES answer to,
per zone band, shipped for the editor's role menu.

    python tools/fmo_npc_keys.py --client <install>            # writes services/fmodata/fmo-npc-keys.tsv
    python tools/fmo_npc_keys.py --client <install> --check    # exit 1 if the generated file is stale

WHY. The editor's "role" menu was a hand list of the nine keys the private
deployment already popped plus the coliseum's. The client's own tables
(AI/F08/D39..D48, one per zone band and nation) key a good deal more: a
ranking board, a communications officer, pilot setup, training, a second desk
of every counter, room and hangar staff. A lobby without them is exactly the
"hollow" a tester described, so the menu is generated from the tables and
says which lobbies each key exists in. The KEY is what makes a counter answer;
the person behind it is the editor's choice.

Columns: key (hex), bands (comma list of hq/occ/fz/col), entries (the SCP
entry names the rows load, comma list), rows (how many rows across the eight
tables). Keys below 0x82000000 are script-internal (battle-end events, BG
events) and are left out: nothing pops under them.
"""
import argparse
import collections
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "fmodatagen"))

OUT = os.path.join(HERE, os.pardir, "services", "fmodata", "fmo-npc-keys.tsv")
#: LEV row -> (event table, band). Both nations' tables carry the same keys;
#: both are read so a key present in only one would still show.
TABLES = ((39, "hq"), (41, "hq"), (40, "occ"), (42, "occ"), (45, "occ"), (46, "occ"),
          (43, "fz"), (47, "fz"), (44, "col"), (48, "col"))


def build(client):
    import fmoprogression as P
    keys = collections.defaultdict(lambda: {"bands": set(), "entries": set(), "rows": 0})
    for k, band in TABLES:
        rel = "AI/F08/D%02d.DAT" % k
        try:
            rows = P.event_rows(rel, client)
        except OSError:
            continue
        for r in rows:
            # kind 1 = an NPC TALK row (the Select-target confirm path); the
            # 4/5/6 kinds are scene entry points and cutscene hooks under
            # 0x820000xx script ids - nothing pops under those
            if r["kind"] != 1 or r["key"] < 0x82080000 or r["key"] >= 0xFFFF0000:
                continue
            e = keys[r["key"]]
            e["bands"].add(band)
            e["entries"].add(r["entry"])
            e["rows"] += 1
    order = ["hq", "occ", "fz", "col"]
    lines = ["key\tbands\tentries\trows"]
    for key in sorted(keys):
        e = keys[key]
        lines.append("0x%08x\t%s\t%s\t%d" % (
            key, ",".join(b for b in order if b in e["bands"]),
            ",".join(sorted(e["entries"])), e["rows"]))
    return "\n".join(lines) + "\n"


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", required=True,
                    help="the FRONT MISSION ONLINE install directory")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    text = build(a.client)
    if a.check:
        try:
            have = open(a.out, encoding="utf-8").read()
        except OSError:
            have = None
        print("current" if have == text else "STALE")
        return 0 if have == text else 1
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    try:
        shown = os.path.relpath(a.out, HERE)
    except ValueError:              # another drive on Windows
        shown = a.out
    print("%s: %d keys" % (shown, text.count("\n") - 1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
