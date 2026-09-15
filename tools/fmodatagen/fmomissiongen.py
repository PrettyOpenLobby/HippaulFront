#!/usr/bin/env python3
"""The mission-catalogue text rules: SE's Japanese titles, zones and form
labels for the O.C.U. and U.S.N. mission lists (AI/F00/D16 and D18) and the
regexes that read a catalogue record's lines. fmoprogression.py joins on
these; standalone this generates the English per-run translation rows.

    python fmomissiongen.py --client <install> --out DIR   # write AI-F00-D16.tsv, AI-F00-D18.tsv
    python fmomissiongen.py --client <install> --report    # list runs no rule covers

KEY: WHY PER-RUN. A catalogue record's text runs are its LINES - the panel is
a form (`Required pilot level:` / `Prerequisite:` / `Victory conditions:` ...).
The translation rows are therefore `index.run` rows, which become
`FILE:index:run` keys and edit one line each. A whole-record edit collapses
every run into the first and would flatten the panel into one line.

WARNING: RUN 0 OF AN OVER-263-BYTE RECORD IS NOT TOUCHED. Such a record cannot
express its length in the header's one length byte, so its second FF-pair is
malformed and `parse_record`'s header scan eats one byte - which is why those
runs read `C務詳細：` and `掃pイロットレベル：` instead of `任務詳細：` and
`受理パイロットレベル：`. Every LATER run in the same record is delimited by control
tokens and parses clean, so those translate normally; run 0 is left verbatim
(the rebuild copies an unedited run byte for byte). It is a header-parse
problem, NOT the "record over 263 bytes is unwritable" rule - `_fix_len`
leaves such a record's length byte alone precisely because it was already
wrong, so the record itself edits fine.

WARNING: AND THE OUTPUT WOULD *LOOK* CLEAN, WHICH IS THE TRAP. Measured on
AI/F00/D16 record 68: a clean 242-byte record's header is
`FE 00 0F FF AF FF 15 FF` (0x15^0xFF = 234 = len-8) and its text starts
`94 43` = cp932 任. Record 68's header is `FE 00 0F FF AF FF F7 FE` - the
length byte cannot hold 264, so the LONE FF that ends a header is absent and
the scan takes `FE 94` as a control token, eating 任's LEAD byte. Rebuild that
record with run 0 replaced and OUR reader shows a clean `Mission details:`,
because it re-eats the same byte - but the file still carries `94` before the
English, and the CLIENT's parser (the correct one) would read `94 4D` as one
double-byte character and shift everything after it. Round-tripping through our
own parser is not evidence here; the byte layout is.
"""
import argparse
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fmofmdt                                                     # noqa: E402
import fmofmdtwrite as W                                           # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

FILES = {"AI/F00/D16.DAT": "AI-F00-D16.tsv",
         "AI/F00/D18.DAT": "AI-F00-D18.tsv"}

TITLES = {
    "敵部隊殲滅作戦": "Enemy Unit Annihilation",
    "実戦訓練": "Live Combat Exercise",
    "敵新型兵器出現": "Enemy Prototype Weapon Sighted",
    "特殊EMP搭載機誘導任務": "Guide the Special EMP Carrier",
    "敵重戦闘ヘリ侵入": "Enemy Heavy Combat Helicopter Incursion",
    "未確認部隊強襲": "Assault the Unidentified Force",
    "敵残存戦力殲滅任務": "Destroy the Enemy Remnants",
    "敵部隊逃亡阻止": "Stop the Enemy Unit Escaping",
    "狙撃部隊潜伏": "Sniper Unit in Hiding",
    "敵部隊迎撃任務": "Intercept the Enemy Unit",
    "反乱分子殲滅任務": "Destroy the Rebels",
    "Damien Rivers追討任務": "Hunt Down Damien Rivers",
    "David Levine追討任務": "Hunt Down David Levine",
    "亡命者回収任務": "Recover the Defectors",
    "爆破装置搭載機強襲": "Assault the Bomb Carriers",
    "敵高速強襲部隊撃退任務": "Repel the Enemy High-Speed Assault Force",
    "戦闘模擬試験": "Mock Combat Test",
    "PMO調査官護衛任務": "Escort the PMO Inspector",
    "大型機動兵器誘導作戦": "Lure the Large Mobile Weapon",
    "敵侵入部隊撃退任務": "Repel the Enemy Incursion",
    "サカタインダストリィ防衛": "Defend Sakata Industry",
    "独立特務機動部隊編入テスト": "Special Mobile Force Induction Test",
    "試作機最終調整テスト": "Prototype Final Adjustment Test",
    "輸送機護衛任務": "Escort the Transport",
    "新型高機動兵器出現": "New High-Mobility Weapon Sighted",
    "敵新型機捕獲作戦": "Capture the Enemy Prototypes",
    "特殊Stealth部隊撃退作戦": "Repel the Special Stealth Unit",
}

ZONES = {"O.C.U.統制区": "O.C.U. Controlled Zone",
         "U.S.N.統制区": "U.S.N. Controlled Zone",
         "O.C.U.占領区": "O.C.U. Occupied Zone",
         "U.S.N.占領区": "U.S.N. Occupied Zone",
         "激戦区": "Frontline Zone"}

CONDS = {
    "敵部隊の全滅": "Destroy every enemy unit",
    "敵部隊の殲滅": "Annihilate the enemy unit",
    "未確認部隊の殲滅": "Annihilate the unidentified force",
    "狙撃部隊の殲滅": "Annihilate the sniper unit",
    "敵残存戦力の殲滅": "Annihilate the enemy remnants",
    "敵高速強襲部隊の殲滅": "Wipe out the enemy high-speed assault force",
    "模擬戦用敵ユニットの殲滅": "Destroy the mock-combat enemy units",
    "爆破装置搭載機以外の敵機の殲滅": "Destroy every enemy except the bomb carriers",
    "反乱分子および敵部隊の殲滅": "Wipe out the rebels and the enemy unit",
    "独立特務機動部隊の撃退": "Repel the Indep. Special Mobile Force",
    "独立特務機動部隊の殲滅": "Wipe out the Indep. Special Mobile Force",
    "大型機動兵器の行動不能": "Disable the large mobile weapon",
    "敵新型兵器の破壊": "Destroy the enemy prototype weapon",
    "大型機動兵器の破壊": "Destroy the large mobile weapon",
    "特殊Stealth部隊の殲滅": "Annihilate the special Stealth unit",
    "制限時間以内に25体の新型機を撃破": "Destroy 25 new machines in the time limit",
    'Type 11 "Raven"の撃破': 'Destroy the Type 11 "Raven"',
    'OSV-XX"Griffin"の撃破': 'Destroy the OSV-XX "Griffin"',
    "PMO調査官搭乗ヘリの戦域外到達": "PMO inspector's helo leaves the combat area",
    "リーダーの撃破": "Leader destroyed",
    "制限時間超過": "Time out",
    "亡命者搭乗機の撃破": "A defector's machine destroyed",
    "指定外模擬戦用敵ユニットの撃破": "A non-designated mock unit destroyed",
    "爆破装置搭載機の撃破": "A bomb carrier destroyed",
    "試作機の撃破": "The prototype destroyed",
    "Ellen Taylor機の撃破": "Ellen Taylor's machine destroyed",
    "Jose Ignacio Navas機の撃破": "Jose Ignacio Navas's machine destroyed",
    "撃破数を満たせずに制限時間超過": "Time out without enough kills",
    "敵重戦闘ヘリの戦域外到達": "Enemy heavy combat helo leaves the combat area",
    "PMO調査官搭乗ヘリの撃破": "PMO inspector's helo destroyed",
}

SECTOR_ZONE = {"統制区": "Ctrl.", "占領区": "Occ.", "激戦区": "Front."}

R_TITLE = re.compile(r"^『(.+?)』$")
R_CLIENT = re.compile(r"^依頼者：(.+)$")
R_AREA = re.compile(r"^任務発生区：(.+)$")
R_LEVEL = re.compile(r"^受理パイロットレベル：(\d+)$")
R_PRE = re.compile(r"^発生条件：(.+)$")
R_BG = re.compile(r"^BGコスト：(\S+)\s*出撃人数制限\(min/max\)：(\d+)/(\d+)$")
R_SECTOR = re.compile(r"^(統制区|占領区|激戦区)(\d+)：セクター(\d+)$")
R_DONE = re.compile(r"^『(.+?)』を終了$")
R_WHERE = re.compile(r"^(統制区|占領区|激戦区)(ロビー|ルーム|ハンガー)\s*：(.+?)\s*$")
PLACES = {"ロビー": "Lobby", "ルーム": "Room", "ハンガー": "Hangar"}
R_PROG = re.compile(r"^任務遂行中：『(.+?)』$")
R_END = re.compile(r"^任務終了\s*：『(.+?)』$")
LABELS = {"勝利条件：": "Win:",
          "敗北条件：": "Lose:",
          "出撃セクター：": "Sectors:",
          "任務詳細：": "Mission details:"}


def conds(run):
    """`　A　B` -> `A / B`, every item from the CONDS table."""
    items = [x for x in run.split("　") if x.strip()]
    if not items or any(x not in CONDS for x in items):
        return None
    return " / ".join(CONDS[x] for x in items)


def translate(run):
    m = R_TITLE.match(run)
    if m and m.group(1) in TITLES:
        return '"%s"' % TITLES[m.group(1)]
    m = R_CLIENT.match(run)
    if m:
        return "Client: %s" % m.group(1)
    m = R_AREA.match(run)
    if m and m.group(1) in ZONES:
        return "Mission area: %s" % ZONES[m.group(1)]
    m = R_LEVEL.match(run)
    if m:
        return "Pilot Lv: %s" % m.group(1)
    m = R_PRE.match(run)
    if m:
        v = m.group(1)
        if v == "-":
            return "Requires: -"
        d = R_DONE.match(v)
        if d and d.group(1) in TITLES:
            return 'Requires: "%s"' % TITLES[d.group(1)]
        return None
    m = R_BG.match(run)
    if m:
        return "BG Cost: %s  Size(min/max): %s/%s" % m.groups()
    m = R_SECTOR.match(run)
    if m:
        return "%s%s: Sec.%s" % (SECTOR_ZONE[m.group(1)], m.group(2), m.group(3))
    m = R_WHERE.match(run)
    if m:
        return "%s %s: %s" % (SECTOR_ZONE[m.group(1)], PLACES[m.group(2)], m.group(3))
    m = R_PROG.match(run)
    if m and m.group(1) in TITLES:
        return 'In progress: "%s"' % TITLES[m.group(1)]
    m = R_END.match(run)
    if m and m.group(1) in TITLES:
        return 'Complete:    "%s"' % TITLES[m.group(1)]
    if run in LABELS:
        return LABELS[run]
    return conds(run)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", required=True,
                    help="the FRONT MISSION ONLINE install directory")
    ap.add_argument("--out", help="directory for the generated per-run tsvs")
    ap.add_argument("--report", action="store_true",
                    help="only list the runs no rule covers")
    a = ap.parse_args()
    if not a.report and not a.out:
        ap.error("--out DIR or --report")
    unmatched = []
    for rel, out in FILES.items():
        dec = fmofmdt.load(a.client, rel)
        _offs, recs = W.split_records(dec)
        rows = []
        for k, rec in enumerate(recs):
            texts = W.text_runs(rec)
            # only the CATALOGUE records - everything else in these two
            # containers is shared with other files
            if not any(R_CLIENT.match(t) or R_PRE.match(t) or t.endswith("詳細：")
                       or R_PROG.match(t) or R_END.match(t) or R_WHERE.match(t)
                       for t in texts):
                continue
            n = 0
            oversize = len(rec) > 263
            for kind, b in [it for _t, _b, its in W.record_blocks(rec)
                            for it in its]:
                if kind != "t":
                    continue
                run = "".join(c for c in b.decode("cp932", "replace")
                              if c >= " ").strip()
                idx, n = n, n + 1
                if not run:
                    continue
                if oversize and idx == 0:
                    continue          # header-parse desync - see the docstring
                en = translate(run)
                if en is None:
                    unmatched.append((rel, k, idx, run))
                    continue
                rows.append("%d.%d\t%s" % (k, idx, en))
        if a.report:
            continue
        os.makedirs(a.out, exist_ok=True)
        path = os.path.join(a.out, out)
        head = ("# The mission catalogue - GENERATED by fmomissiongen.py.\n"
                "# Hand-written rows may follow the marker; regenerating keeps them.\n")
        keep = ""
        if os.path.exists(path):
            old = io.open(path, encoding="utf-8").read()
            if "# --- hand-written ---" in old:
                keep = "\n# --- hand-written ---\n" + \
                    old.split("# --- hand-written ---", 1)[1].lstrip("\n")
        io.open(path, "w", encoding="utf-8", newline="").write(
            head + "\n".join(rows) + "\n" + keep)
        print("%s: %d generated row(s)" % (out, len(rows)))
    if unmatched:
        print("\n%d run(s) no rule covers:" % len(unmatched))
        for rel, k, n, run in unmatched:
            print("  %s %d.%d  %s" % (rel, k, n, run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
