#!/usr/bin/env python3
"""The story missions' own battles: per mission byte, the enemies, the allied
units, the objective and the win / loss conditions, as fmo-story-battles.tsv.

    python fmostorybattles.py --client <install> --missions <fmo-missions.tsv> --out DIR
    python fmostorybattles.py --client <install> --missions <fmo-missions.tsv> --check

TWO SOURCES:

  * the mission catalogue  AI/F00/D16 (O.C.U.) / AI/F00/D18 (U.S.N.), MSG
    records (fmoprogression / fmomissiongen read the same records): for each
    mission its form record carries the required Pilot level, the B.G. cost,
    the sortie size (min/max), the VICTORY CONDITIONS (勝利条件) and the
    DEFEAT CONDITIONS (敗北条件), and the record after it the mission details
    (任務詳細), which name the enemy ("six red-armoured wanzers", "a heavy
    combat helicopter with two escort helicopters", "destroy 25 new machines
    within the time limit", "escort the transport", ...).
  * COMPOSITION below: OUR reading of each mission's details into units the
    server can pop. The conditions come from the client; the unit counts,
    HP scales, the time limits the details do not state and the names are
    OURS and each row says what the details gave it.

THE COLUMNS the server reads (fmoserver/storybattle.py):
  objective   annihilate      win when every enemy unit (every wave) is down
              target          win when every unit marked ! is down
              spare           win when every enemy but the ~ ones is down; a ~
                              unit destroyed is a loss
              count:N:F       win at N enemies destroyed, F on the field at a
                              time (a new one arrives for each one down)
              order           enemies must be destroyed in call-sign order;
                              any other one destroyed is a loss
              survive         win when the time limit runs out with every !
                              ally alive (or when every enemy is down)
  enemies     space-separated units, '|' between waves (the next wave pops
              when the one before it is down). A unit is
              <kind>[*n][@hp%][!|~][=name]; kinds: wanzer, sniper, jammer,
              recon, comms (NPC wanzer sets by role), tank, heli, heli_heavy
              (the toughest helicopter set), boss, boss_big (the large mobile
              weapon frames, toughest first). ! marks an objective target (the
              POP's target flag, block kind 1 names it on the HUD); ~ a unit
              that must NOT be destroyed.
  allies      the same grammar for units on the pilot's side; ! = protected:
              its loss loses the mission (block kind 2 names it on the HUD).
  time_limit  seconds; empty = the server's FMO_MISSION_TIME.
  timeout     loss / win: what the time limit running out means.
  block_kind  the mission block's objective kind (+0x50): 0 none, 1 "Target
              unit %s confirmed. Destroy it." (79:12), 2 "Unit %s has been set
              as the enemy's target. Defend it." (79:13).
"""
import argparse
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fmomissiongen as M                                          # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

CATALOGUE = {"O.C.U.": "AI/F00/D16.DAT", "U.S.N.": "AI/F00/D18.DAT"}
#: the form record's level run; an over-263-byte record eats a byte of it
#: (fmomissiongen's docstring), so only the tail is matched
LVL = re.compile(r"イロットレベル：(\d+)")
WIN, LOSE, SECTORS = "勝利条件：", "敗北条件：", "出撃セクター："
DETAILS = re.compile(r"務詳細：$")

#: OUR reading of each mission's details (任務詳細), keyed by the catalogue's
#: Japanese title. "said" is what the client's own details text states; the
#: rest of each row is ours. Missions that share a title across the two
#: catalogues share a row; "names" overrides a unit name per faction.
COMPOSITION = {
    "敵部隊殲滅作戦": dict(
        objective="annihilate", enemies="wanzer*6",
        said="six red-armoured wanzers near the base"),
    "実戦訓練": dict(
        objective="annihilate", enemies="wanzer*6@120",
        said="a live exercise against the elite 17th Mobile Squadron"),
    "敵新型兵器出現": dict(
        objective="target", enemies="boss_big!=Prototype wanzer*2", block_kind=1,
        said="the enemy prototype weapon; the mercenaries who went ahead were lost"),
    "特殊EMP搭載機誘導任務": dict(
        objective="annihilate", enemies="wanzer*4@150",
        allies="wanzer!=Ally.Lead jammer=EMP.Carrier", block_kind=2,
        said="new machines whose coating nullifies attacks; guide the EMP carrier; "
             "a joint operation (defeat: the allied leader's machine destroyed)"),
    "敵重戦闘ヘリ侵入": dict(
        objective="annihilate", enemies="heli_heavy!=Heavy.Heli heli*2", block_kind=1,
        time_limit=600, timeout="loss",
        said="a heavy combat helicopter and two escort helicopters (defeat: it "
             "leaves the combat area)"),
    "未確認部隊強襲": dict(
        objective="annihilate", enemies="wanzer*4",
        said="an unidentified force of unknown strength that ambushed a platoon"),
    "敵残存戦力殲滅任務": dict(
        objective="annihilate", enemies="recon wanzer*3",
        said="mostly long-range rear units; detection by enemy radar brings missiles"),
    "敵部隊逃亡阻止": dict(
        objective="annihilate", enemies="jammer*4",
        said="the unit that stole the EMP backpacks; beware EMP status attacks"),
    "狙撃部隊潜伏": dict(
        objective="annihilate", enemies="sniper*4",
        said="a sniper unit sent to assassinate the general"),
    "敵部隊迎撃任務": dict(
        objective="annihilate", enemies="jammer*2 comms*2",
        said="an EMP-armed electronic warfare special unit"),
    "反乱分子殲滅任務": dict(
        objective="annihilate", enemies="wanzer*4 | wanzer*3",
        said="the rebels raiding the convoy, and an enemy unit expected to appear"),
    "Damien Rivers追討任務": dict(
        objective="annihilate", enemies="comms!@200=Commander wanzer*4 | wanzer*3",
        block_kind=1,
        said="the special mobile force and its commander; reinforcements expected; "
             "attack the commander's machine with the comms gear first"),
    "David Levine追討任務": dict(
        objective="annihilate", enemies="comms!@200=Commander wanzer*4 | wanzer*3",
        block_kind=1,
        said="the special mobile force and its commander; reinforcements expected; "
             "attack the commander's machine with the comms gear first"),
    "亡命者回収任務": dict(
        objective="spare", enemies="wanzer*4 wanzer~*2=Defector",
        said="enemy pilots defecting: destroy every other enemy (defeat: a "
             "defector's machine destroyed)"),
    "爆破装置搭載機強襲": dict(
        objective="spare", enemies="wanzer*4 wanzer~*2=Bomb.Carrier",
        said="destroy every enemy but the bomb carriers (defeat: a bomb carrier "
             "destroyed)"),
    "敵高速強襲部隊撃退任務": dict(
        objective="annihilate", enemies="wanzer*6@120",
        said="a high-speed assault force on special hovers"),
    "戦闘模擬試験": dict(
        objective="order", enemies="wanzer*5",
        said="destroy the mock-combat units in call-sign order from 11 (defeat: "
             "a non-designated unit destroyed)"),
    "PMO調査官護衛任務": dict(
        objective="survive", enemies="wanzer*4", allies="heli!@200=PMO.Helo",
        block_kind=2, time_limit=480, timeout="win",
        said="escort the inspector's helicopter (victory: it leaves the combat "
             "area; defeat: it is destroyed)"),
    "大型機動兵器誘導作戦": dict(
        objective="target", enemies="boss_big!@150=Large.Weapon", block_kind=1,
        said="a large mobile weapon that wrecked the allied unit"),
    "敵侵入部隊撃退任務": dict(
        objective="annihilate", enemies="wanzer*4 | wanzer*3",
        said="intercept the enemy before the contact; reinforcements likely"),
    "サカタインダストリィ防衛": dict(
        objective="annihilate", enemies="wanzer*4 tank*2 | wanzer*3",
        said="a large-scale battle near the Sakata Industry facility"),
    "独立特務機動部隊編入テスト": dict(
        objective="annihilate", enemies="wanzer*3@150",
        said="beat members of the special mobile force in a live exercise"),
    "試作機最終調整テスト": dict(
        objective="annihilate", enemies="wanzer*5", allies="wanzer!@200=Prototype",
        block_kind=2,
        said="guard the prototype under test (defeat: the prototype destroyed)"),
    "輸送機護衛任務": dict(
        objective="annihilate", enemies="wanzer*5", allies="heli!@200=Transport",
        block_kind=2,
        said="escort the disguised transport, which cannot survive an attack; "
             "reach the rendezvous within 4 minutes of the sortie"),
    "新型高機動兵器出現": dict(
        objective="target", enemies="boss_big!@200=Raven wanzer*4", block_kind=1,
        names={"U.S.N.": {"Raven": "Griffin"}},
        said="the new high-mobility weapon in the ruins, a supply point with a "
             "large enemy force and mines"),
    "敵新型機捕獲作戦": dict(
        objective="count:25:4", enemies="wanzer*25@150", time_limit=600, timeout="loss",
        said="destroy 25 new machines before the operatives sortie 10 minutes in; "
             "their defence nullifies two attack attributes"),
    "特殊Stealth部隊撃退作戦": dict(
        objective="annihilate", enemies="recon*2 wanzer*3",
        said="the stealth unit that ambushed the platoon"),
}

COLUMNS = ("faction", "own_byte", "title", "level", "size", "bg_cost", "win", "lose",
           "objective", "enemies", "allies", "time_limit", "timeout", "block_kind", "said")

UNIT = re.compile(r"^(wanzer|sniper|jammer|recon|comms|tank|heli|heli_heavy|boss|boss_big)"
                  r"((?:\*\d+|@\d+|[!~])*)(?:=([A-Za-z0-9.]{1,16}))?$")
MOD = re.compile(r"\*(\d+)|@(\d+)|([!~])")


def parse_units(spec):
    """'heli_heavy!=X heli*2 | wanzer*3' -> [[unit, ...], [unit, ...]] (waves);
    a unit is {kind, hp, mark, name}, expanded per count; the *n, @hp and !/~
    modifiers come in any order. Raises ValueError."""
    waves = []
    for wave in (spec or "").split("|"):
        units = []
        for tok in wave.split():
            m = UNIT.match(tok)
            if not m:
                raise ValueError("unit %r: want kind[*n][@hp][!|~][=name]" % tok)
            n, hp, mark = 1, 100, ""
            for c, h, k in MOD.findall(m.group(2)):
                if c:
                    n = int(c)
                if h:
                    hp = int(h)
                if k:
                    mark = k
            for _ in range(n):
                units.append({"kind": m.group(1), "hp": hp, "mark": mark,
                              "name": m.group(3) or ""})
        if units:
            waves.append(units)
    return waves


def conditions(runs):
    """(win items, lose items) from one form record's runs (Japanese)."""
    win, lose, cur = [], [], None
    for r in runs:
        if r == WIN:
            cur = win
            continue
        if r == LOSE:
            cur = lose
            continue
        if r == SECTORS:
            cur = None
            continue
        if cur is not None:
            cur.extend(x for x in r.split("　") if x.strip())
    return win, lose


def catalogue(client, faction):
    """[{title_jp, title, level, size, bg, win, lose}] for one catalogue."""
    import fmofmdt
    import fmofmdtwrite as W
    dec = fmofmdt.load(client, CATALOGUE[faction])
    _o, recs = W.split_records(dec)
    runs = [W.text_runs(r) for r in recs]
    out, cur = [], None
    for rs in runs:
        rs = [x.strip() for x in rs if x and x.strip()]
        if not rs:
            continue
        m = M.R_TITLE.match(rs[0])
        if m and len(rs) > 1 and M.R_CLIENT.match(rs[1]):
            cur = {"title_jp": m.group(1), "title": M.TITLES.get(m.group(1), m.group(1))}
            continue
        if cur is None or not any(LVL.search(x) for x in rs):
            continue
        lv = next(int(LVL.search(x).group(1)) for x in rs if LVL.search(x))
        bg = size = ""
        for x in rs:
            b = M.R_BG.match(x)
            if b:
                bg, size = b.group(1), "%s/%s" % (b.group(2), b.group(3))
        win, lose = conditions(rs)
        cur.update(level=lv, bg=bg, size=size, win=win, lose=lose)
        out.append(cur)
        cur = None
    return out


def own_bytes(path):
    """{(faction, English title): own byte} from fmo-missions.tsv."""
    out = {}
    with io.open(path, encoding="utf-8") as f:
        cols = f.readline().rstrip("\r\n").split("\t")
        for line in f:
            r = dict(zip(cols, line.rstrip("\r\n").split("\t")))
            if r.get("own_byte", "").strip():
                out[(r["faction"], r["title"])] = int(r["own_byte"])
    return out


def cond_text(items):
    return " / ".join(M.CONDS.get(x, x) for x in items)


def rows_for(client, missions_tsv):
    """The table rows, and the problems (a catalogue mission with no own byte
    or no COMPOSITION row, or a row that does not parse)."""
    owns = own_bytes(missions_tsv)
    rows, problems = [], []
    for faction in CATALOGUE:
        for m in catalogue(client, faction):
            comp = COMPOSITION.get(m["title_jp"])
            own = owns.get((faction, m["title"]))
            if comp is None:
                problems.append("%s %s: no COMPOSITION row" % (faction, m["title_jp"]))
                continue
            if own is None:
                problems.append("%s %s: no own byte in fmo-missions.tsv" % (faction, m["title"]))
                continue
            enemies = comp["enemies"]
            allies = comp.get("allies", "")
            for old, new in (comp.get("names") or {}).get(faction, {}).items():
                enemies = enemies.replace("=" + old, "=" + new)
                allies = allies.replace("=" + old, "=" + new)
            try:
                parse_units(enemies)
                parse_units(allies)
            except ValueError as e:
                problems.append("%s %s: %s" % (faction, m["title"], e))
                continue
            rows.append({
                "faction": faction, "own_byte": own, "title": m["title"],
                "level": m["level"], "size": m["size"], "bg_cost": m["bg"],
                "win": cond_text(m["win"]), "lose": cond_text(m["lose"]),
                "objective": comp["objective"], "enemies": enemies, "allies": allies,
                "time_limit": comp.get("time_limit", ""), "timeout": comp.get("timeout", "loss"),
                "block_kind": comp.get("block_kind", 0), "said": comp["said"]})
    return rows, problems


def write(rows, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "fmo-story-battles.tsv")
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\t".join(COLUMNS) + "\n")
        for r in sorted(rows, key=lambda x: (x["faction"], x["own_byte"])):
            f.write("\t".join(str(r[c]) for c in COLUMNS) + "\n")
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", required=True,
                    help="the FRONT MISSION ONLINE install directory")
    ap.add_argument("--missions", required=True,
                    help="fmo-missions.tsv (fmoprogression.py) for the own bytes")
    ap.add_argument("--out", help="directory for fmo-story-battles.tsv")
    ap.add_argument("--check", action="store_true", help="the join only, no file")
    a = ap.parse_args()
    if not a.check and not a.out:
        ap.error("--out DIR or --check")
    rows, problems = rows_for(a.client, a.missions)
    for p in problems:
        print("PROBLEM: " + p)
    print("%d story battle row(s)" % len(rows))
    if not a.check:
        print("wrote " + write(rows, a.out))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
