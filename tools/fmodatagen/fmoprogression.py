#!/usr/bin/env python3
"""The FMO campaign as DATA: which cutscene plays for whom, gated by what,
in what order - joined from the game's own tables, never typed by hand.

    python fmoprogression.py --client <install> --out DIR   # writes fmo-missions.tsv, fmo-cutscenes.tsv
    python fmoprogression.py --client <install> --check     # the join only, no files

THREE SOURCES, each read by its own already-proven parser:

  * the mission catalogue  AI/F00/D16 (O.C.U.) / AI/F00/D18 (U.S.N.), MSG
    records: title, required pilot level, PREREQUISITE ("complete X"), B.G.
    cost, platoon size, client NPC, area, sortie sectors (fmomissiongen rules)
  * the NPC event tables   AI/F08/D39..D48, 0x54-byte rows: entity key, rank
    window, flag bits that must be set, `byte[i]==v` tests, SCP id, entry name
    (the row layout from the cutscene-trigger analysis)
  * the LEV table          AI/F08/D15: zone band -> nation -> event table row,
    which is what says D39 is the O.C.U. controlled lobby and D41 the U.S.N.

THE JOIN. An SCP id k's dialogue is resource 79863+2k+1 (253/253 measured),
so every gated row names the scene it plays. And the rank window's LOW edge
is the mission's required pilot level - six independent agreements across
both tables, each carrying its prerequisite with it. So for each gated row:
the mission(s) of that faction whose level equals `rank_lo` are the candidates;
a `byte[i]==99` test is that mission's PREREQUISITE's progress byte and a
`byte[j]==0/1/2/3` test is its OWN. When the candidate is unique AND the
prerequisite byte resolves (by the same rule at the prerequisite's level) to
the mission the catalogue names as the prerequisite, the assignment is marked
`confirmed`; otherwise it is listed as `candidate` and left for a live walk.
Nothing here guesses past what the two tables agree on.

2026-09-11: a THIRD pass (propagate) applies the same row shape to a fixpoint
- a unique candidate's ==99 byte names its PREREQUISITE's byte, a known byte
rules a candidate in or out at a two-mission level, a ==99 test on a mission's
own byte is its epilogue. Rows it resolves are labelled `inferred (why)` and
the missions table says per byte whether it came from the `table` directly or
was `inferred` (own_conf). The server's frontier consumes both alike; the
label is so the screen can be asked about the right ones first.

WARNING: STATIC. No cutscene in this table has been played through on screen.
"""
import argparse
import collections
import io
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fmofmdt                                                     # noqa: E402
import fmofmdtwrite as W                                           # noqa: E402
import fmomissiongen as M                                          # noqa: E402
import fmofile                                                     # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

CATALOGUE = {1: "AI/F00/D16.DAT", 2: "AI/F00/D18.DAT"}
LEV = "AI/F08/D15.DAT"
EVT_BASE = 80839          # LEV row 0x900k -> resource 80839 + k (0x610ECE4A)
NATION_NAME = {1: "O.C.U.", 2: "U.S.N.", 0: "shared"}
LVL = re.compile(r"イロットレベル：(\d+)$")   # matches the desynced run 0 too

#: The story arc a dialogue container belongs to: our own English labels for
#: SE's scene files (the header lines of the translation tables), keyed by the
#: dialogue resource the cutscene row names. "Operator" in a label is the
#: in-game radio Operator character.
ARCS = {
    "AH/F98/D84.DAT": "Training ground",
    "AH/F98/D88.DAT": "Control-scheme overlay shown during the piloting training",
    "AH/F98/D92.DAT": "The prototype combat-test arc",
    "AH/F98/D96.DAT": "The B-Device conspiracy",
    "AH/F99/D00.DAT": "Nina's tour of the base",
    "AH/F99/D06.DAT": 'The "capture the new model" arc',
    "AH/F99/D10.DAT": "Frontline-Zone mission briefings and debriefs (Operator + officers)",
    "AH/F99/D50.DAT": "THE COLISEUM (arena) help text",
    "AH/F99/D54.DAT": "Occupied-zone base greeting",
    "AI/F00/D10.DAT": "The Sakata Industry facility defense",
    "AI/F00/D12.DAT": "The special-force induction test",
    "AI/F00/D16.DAT": "The O.C.U. mission board's `任務詳細` briefs",
    "AI/F00/D18.DAT": "The U.S.N. mission board's `任務詳細` briefs",
    "AI/F00/D20.DAT": "The Forster / Miller arc",
    "AI/F00/D24.DAT": "The Freedom operation + Balfour's break",
    "AI/F00/D26.DAT": "Frontline-Zone mission-type explanations (the Operator's rules briefing)",
    "AI/F00/D98.DAT": "The First Sergeant's live-fire exercise briefing",
    "AI/F01/D00.DAT": "The base-life arc",
    "AI/F01/D04.DAT": "Cpl. Harada / Burnet's arc",
    "AI/F01/D08.DAT": "The heavy combat helicopter arc",
    "AI/F01/D10.DAT": "The B-Device arc",
    "AI/F01/D16.DAT": "The information broker's arc",
    "AI/F01/D20.DAT": "The Raven / Griffin story arc",
    "AI/F01/D22.DAT": "Sassoon / Wright's arc",
    "AI/F01/D28.DAT": "End of the piloting training",
    "AI/F01/D30.DAT": "Operator radio calls",
    "AI/F01/D32.DAT": "Objective results",
    "AI/F01/D36.DAT": "Objective results",
    "AI/F02/D20.DAT": "The Lark Valley briefing",
    "AI/F08/D18.DAT": "THE OPENING NARRATION",
}


def decoded(rel, client):
    return fmofmdt.load(client, rel)


def runs_of(rel, client):
    dec = decoded(rel, client)
    _o, recs = W.split_records(dec)
    return {k: W.text_runs(rec) for k, rec in enumerate(recs)}


def missions(nation, client):
    """[{title, level, pre, bg, size, client, area, sectors}] for one faction."""
    out, cur = [], None
    for k, rs in sorted(runs_of(CATALOGUE[nation], client).items()):
        if not rs:
            continue
        m = M.R_TITLE.match(rs[0])
        if m and m.group(1) in M.TITLES and len(rs) > 1 and M.R_CLIENT.match(rs[1]):
            area = M.R_AREA.match(rs[2]) if len(rs) > 2 else None
            cur = {"title": M.TITLES[m.group(1)], "rec": k,
                   "client": M.R_CLIENT.match(rs[1]).group(1),
                   "area": M.ZONES.get(area.group(1), "?") if area else "?"}
            continue
        if cur is None:
            continue
        lvl = pre = bg = size = None
        secs = []
        for r in rs:
            mm = LVL.search(r)
            if mm:
                lvl = int(mm.group(1))
            mm = M.R_PRE.match(r)
            if mm:
                v = mm.group(1)
                d = M.R_DONE.match(v)
                pre = None if v == "-" else (M.TITLES.get(d.group(1), d.group(1)) if d else v)
            mm = M.R_BG.match(r)
            if mm:
                bg, size = mm.group(1), "%s/%s" % (mm.group(2), mm.group(3))
            mm = M.R_SECTOR.match(r)
            if mm:
                secs.append("%s%s:S%s" % (M.SECTOR_ZONE[mm.group(1)], mm.group(2), mm.group(3)))
        if lvl is not None or pre is not None:
            cur.update(level=lvl, pre=pre, bg=bg, size=size, sectors=secs)
            out.append(cur)
            cur = None
    return out


def lev_rows(client):
    """[(kind_lo, kind_hi, nation, event_table_rel, scp, flag, entry)]"""
    dec = decoded(LEV, client)
    out = []
    for i in range(13):
        r = dec[0x88 + i * 0x54:0x88 + (i + 1) * 0x54]
        klo, khi, nat, row, scp, flag = struct.unpack_from("<HHIIII", r, 0)
        entry = r[0x14:0x34].split(b"\0")[0].decode("latin1")
        rel = fmofile.rel_of(EVT_BASE + (row & 0xFFF))
        out.append((klo, khi, nat, rel, scp, flag, entry))
    return out


def event_rows(rel, client):
    """Every 0x54-byte row of one NPC event table, in file order."""
    dec = decoded(rel, client)
    rows = []
    # rows are scanned by shape, so a table with a different header does not
    # silently yield nothing
    for o in range(0, len(dec) - 0x54, 4):
        kind = struct.unpack_from("<I", dec, o + 4)[0]
        if kind not in (1, 4, 5, 6):
            continue
        name = dec[o + 0x2C:o + 0x4C]
        if not re.match(rb"^[A-Za-z_][A-Za-z0-9_]{2,30}\x00", name):
            continue
        key, _k, rlo, rhi, f1, f2, b1, v1, b2, v2, scp = struct.unpack_from("<11I", dec, o)
        if scp & 0xF000 not in (0x8000, 0xA000):
            continue
        rows.append(dict(key=key, kind=kind, rank_lo=rlo, rank_hi=rhi,
                         flags=[f for f in (f1, f2) if f],
                         tests=[(b, v) for b, v in ((b1, v1), (b2, v2)) if b],
                         scp=scp, entry=name.split(b"\0")[0].decode("latin1")))
    return rows


def dialogue_for(scp):
    idx = 79863 + 2 * (scp & 0xFFF)
    return fmofile.rel_of(idx), fmofile.rel_of(idx + 1)


def build(client):
    ms = {n: missions(n, client) for n in (1, 2)}
    by_level = {n: collections.defaultdict(list) for n in (1, 2)}
    for n in (1, 2):
        for m in ms[n]:
            if m["level"] is not None:
                by_level[n][m["level"]].append(m)
    lev = lev_rows(client)
    table_nation, table_kinds = {}, {}
    for klo, khi, nat, rel, _scp, _flag, _entry in lev:
        # rows 0x900a/0x900b are SE's DEBUG lobbies (kinds 700..719, nation
        # 0) and their event tables D49/D50 do not ship; skip what is absent
        if not os.path.exists(fmofile.data_path(client, rel)):
            continue
        table_nation.setdefault(rel, nat)
        table_kinds.setdefault(rel, "%d..%d" % (klo, khi))
    # byte -> mission, by the rule; filled as we go, then used for prereq checks
    own_byte = {1: {}, 2: {}}
    cutscenes = []
    for rel in sorted(table_nation):
        nat = table_nation[rel]
        for r in event_rows(rel, client):
            gated = r["rank_lo"] or r["flags"] or r["tests"]
            if not gated:
                continue
            script, dlg = dialogue_for(r["scp"])
            cands = by_level.get(nat, {}).get(r["rank_lo"], []) if nat in (1, 2) else []
            pre_b = [b for b, v in r["tests"] if v == 99]
            own_b = [b for b, v in r["tests"] if v != 99]
            row = dict(faction=NATION_NAME.get(nat, "?"), zone_table=rel,
                       zone_kinds=table_kinds[rel], entity_key="0x%08x" % r["key"],
                       entry=r["entry"], rank=("%d-%d" % (r["rank_lo"], r["rank_hi"])
                                              if r["rank_lo"] else ""),
                       flag_bits=",".join(str(f) for f in r["flags"]),
                       byte_tests=" & ".join("byte[%d]==%d" % t for t in r["tests"]),
                       scp="0x%04x" % r["scp"], script=script, dialogue=dlg,
                       arc=ARCS.get(dlg, ""),
                       candidates=" | ".join(m["title"] for m in cands),
                       confidence="")
            # machine-readable copies for propagate(); write_tsv never emits them
            row["_cands"], row["_pre_b"], row["_own_b"] = list(cands), pre_b, own_b
            if len(cands) == 1 and nat in (1, 2):
                m = cands[0]
                for b in own_b:
                    own_byte[nat].setdefault(b, m["title"])
                # confirmed when the ==99 byte is already known to be the
                # catalogue's prerequisite, or when the prerequisite's level
                # resolves uniquely to a mission with that byte as its own
                conf = "candidate"
                if pre_b and m["pre"]:
                    pre_ms = [x for x in ms[nat] if x["title"] == m["pre"]]
                    if pre_ms and any(own_byte[nat].get(b) == m["pre"] for b in pre_b):
                        conf = "confirmed"
                        for b in pre_b:
                            own_byte[nat].setdefault(b, m["pre"])
                elif not pre_b and own_b:
                    conf = "own-byte only"
                row["confidence"] = conf
            elif cands:
                row["confidence"] = "ambiguous (%d at this level)" % len(cands)
            cutscenes.append(row)
    # second pass: rows whose ==99 byte became known after they were seen
    for row in cutscenes:
        if row["confidence"] == "candidate" and row["byte_tests"]:
            nat = {"O.C.U.": 1, "U.S.N.": 2}.get(row["faction"])
            m = [x for x in ms[nat] if x["title"] == row["candidates"]]
            pre_b = [int(b) for b in re.findall(r"byte\[(\d+)\]==99", row["byte_tests"])]
            if m and m[0]["pre"] and any(own_byte[nat].get(b) == m[0]["pre"] for b in pre_b):
                row["confidence"] = "confirmed"
    inferred = propagate(cutscenes, ms, own_byte)
    mission_rows = []
    for nat in (1, 2):
        inv = {}
        for b, t in own_byte[nat].items():
            inv.setdefault(t, b)          # first assignment wins; a second is a conflict
        for m in ms[nat]:
            ob = inv.get(m["title"], "")
            mission_rows.append(dict(
                faction=NATION_NAME[nat], title=m["title"], level=m["level"],
                prerequisite=m["pre"] or "", own_byte=ob,
                own_conf=("inferred" if ob != "" and ob in inferred[nat] else
                          "table" if ob != "" else ""),
                prereq_byte=inv.get(m["pre"], "") if m["pre"] else "",
                bg_cost=m["bg"] or "", size=m["size"] or "", client=m["client"],
                area=m["area"], sectors=", ".join(m["sectors"])))
    return mission_rows, cutscenes


def propagate(cutscenes, ms, own_byte):
    """THIRD PASS (2026-09-11): SE's row shape, applied to a FIXPOINT.

    The first two passes only read a row whose candidate is unique AND whose
    ==99 byte is already known - which left the Induction Test (Lv29) with no
    own byte although the Lv32 row `byte[177]==99 & byte[179]==0` names it
    (its candidate is unique and 179 is the Final Adjustment Test's own byte),
    and left every level that holds two missions (31, 41) `ambiguous` even
    where one candidate's known byte rules it out. The server's frontier
    (fmo.py progress_frontier) walks the chain by these bytes, so a blank
    here is where the campaign STALLS after Sakata -> Induction.

    Per row, with candidates C (missions at rank_lo), P = the ==99 bytes,
    O = the !=99 bytes, and `byte(title)` the bytes assigned so far:
      * EPILOGUE  - a ==99 test on a candidate's OWN byte is "this mission is
        done", not a prerequisite gate (the 179==99 row at rank 32 replays the
        prototype-arc ending). Labelled, assigns nothing.
      * POSITIVE  - a candidate whose own byte is in O, or whose prerequisite's
        byte is in P, IS the row's mission.
      * ELIMINATE - a candidate whose known own byte is not in O, or whose
        known prerequisite byte is not in P, or which has no prerequisite while
        the row tests one, is NOT the row's mission.
      * exactly one candidate left -> its own byte := O, its PREREQUISITE's own
        byte := P, each only when unassigned; a byte already assigned to a
        different mission is a MISMATCH and is reported, never overwritten.
    Repeats until nothing changes. Rows resolved here are labelled `inferred
    (why)`; `confirmed` keeps its pass-1 meaning. Nothing is typed by hand.

    Checked against the dialogue each script plays (the dialogue translation
    table): the 157 series (rank 41, D39) says "attack Damien Rivers' machine
    first" = Hunt Down Damien Rivers; the 155 series (rank 41, D40) says
    "destroy the rebels ... recover the cargo the transport unit was carrying"
    = Destroy the Rebels; 0x80b6 at rank 31 (163/165) speaks of "extraordinary
    mobility" = the High-Speed Assault Force. WARNING: an earlier reading of
    the trigger tables listed 157/158 as Escort the Transport and 181/182 as
    Destroy the Rebels; both the row shape and the dialogue say 157 = Hunt
    Down Rivers/Levine, 155/156 = Destroy the Rebels, 181/182 = Escort the
    Transport.
    Returns {nation: set(bytes assigned by this pass)}."""
    by_title = {n: {m["title"]: m for m in ms[n]} for n in (1, 2)}
    inferred = {1: set(), 2: set()}
    SKIP = ("confirmed", "epilogue", "mismatch", "inferred", "own-byte only")
    changed = True
    while changed:
        changed = False
        for row in cutscenes:
            nat = {"O.C.U.": 1, "U.S.N.": 2}.get(row["faction"])
            cands = row.get("_cands") or []
            if not nat or not cands or row["confidence"].startswith(SKIP):
                continue
            tb = {}
            for b, t in own_byte[nat].items():
                tb.setdefault(t, b)
            pre_b, own_b = row["_pre_b"], row["_own_b"]
            if pre_b and not own_b:
                epi = [m for m in cands if tb.get(m["title"]) in pre_b]
                if epi:
                    row["confidence"] = ("epilogue (byte[%d] is %s's own byte)"
                                         % (tb[epi[0]["title"]], epi[0]["title"]))
                    changed = True
                    continue
            pos = [m for m in cands
                   if tb.get(m["title"]) in own_b
                   or (m["pre"] and tb.get(m["pre"]) in pre_b)]
            keep, why = [], []
            if len(pos) == 1:
                keep = pos
                m = pos[0]
                why.append("own byte %d is known" % tb[m["title"]]
                           if tb.get(m["title"]) in own_b else
                           "%s's byte %d is known" % (m["pre"], tb[m["pre"]]))
            else:
                for m in cands:
                    ob = tb.get(m["title"])
                    pb = tb.get(m["pre"]) if m["pre"] else None
                    if own_b and ob is not None and ob not in own_b:
                        why.append("not %s: its own byte is %d" % (m["title"], ob))
                    elif pre_b and pb is not None and pb not in pre_b:
                        why.append("not %s: its prerequisite's byte is %d" % (m["title"], pb))
                    elif pre_b and not m["pre"]:
                        why.append("not %s: it has no prerequisite" % m["title"])
                    else:
                        keep.append(m)
            if not keep and len(cands) == 1 and why:
                # the only candidate contradicts a byte assigned from another
                # row: the two tables disagree, and that is worth a line
                row["confidence"] = "mismatch (%s)" % "; ".join(why)
                changed = True
                continue
            if len(keep) != 1:
                continue
            m = keep[0]
            new, bad = [], []

            def assign(b, title):
                cur = own_byte[nat].get(b)
                if cur is None:
                    own_byte[nat][b] = title
                    inferred[nat].add(b)
                    return True
                return cur == title

            for b in own_b:
                if tb.get(m["title"]) is None:
                    (new if assign(b, m["title"]) else bad).append(
                        "%s = byte %d" % (m["title"], b))
            for b in pre_b:
                if m["pre"] and tb.get(m["pre"]) is None:
                    (new if assign(b, m["pre"]) else bad).append(
                        "%s = byte %d" % (m["pre"], b))
            if bad:
                row["confidence"] = "mismatch (%s already assigned elsewhere)" % "; ".join(bad)
            elif new or len(cands) > 1:
                row["confidence"] = "inferred (%s)" % "; ".join(new + why)
                row["candidates"] = m["title"]
            else:
                row["confidence"] = "confirmed"
            changed = True
    return inferred


def write_tsv(path, rows, cols):
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        f.write("\t".join(cols) + "\n")
        for r in rows:
            f.write("\t".join(str(r.get(c, "")) for c in cols) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", required=True,
                    help="the FRONT MISSION ONLINE install directory")
    ap.add_argument("--out", default=".",
                    help="directory for fmo-missions.tsv and fmo-cutscenes.tsv")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    mission_rows, cutscenes = build(a.client)
    conf = collections.Counter(r["confidence"] or "ungated/unmatched" for r in cutscenes)
    print("missions: %d  cutscene rows: %d  %s" % (len(mission_rows), len(cutscenes), dict(conf)))
    assigned = sum(1 for r in mission_rows if r["own_byte"] != "")
    print("missions with a progress byte assigned: %d of %d" % (assigned, len(mission_rows)))
    if a.check:
        return 0
    os.makedirs(a.out, exist_ok=True)
    out_missions = os.path.join(a.out, "fmo-missions.tsv")
    out_cutscenes = os.path.join(a.out, "fmo-cutscenes.tsv")
    write_tsv(out_missions, mission_rows,
              ["faction", "title", "level", "prerequisite", "own_byte", "prereq_byte",
               "own_conf", "bg_cost", "size", "client", "area", "sectors"])
    write_tsv(out_cutscenes, cutscenes,
              ["faction", "zone_table", "zone_kinds", "entity_key", "entry", "rank",
               "flag_bits", "byte_tests", "scp", "script", "dialogue", "arc",
               "candidates", "confidence"])
    print("wrote", out_missions, "and", out_cutscenes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
