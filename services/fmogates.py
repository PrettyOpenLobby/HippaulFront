#!/usr/bin/env python3
"""fmogates.py -- FMO's story gates, as a tool: read and set the progress
flags, rank and mission states that decide which cutscene, NPC conversation
and unlock a pilot gets next.

    python fmogates.py --selftest

WHY. The campaign is gated by data the client carries (the NPC event tables
AI/F08/D39..D49, the mission catalogue, the scripts' own flag tests) against
state the SERVER owns (the pilot's 256-byte flag block and rank, see
fmostore). Working out the ladder means setting that state, talking to an NPC
and watching which scene plays. Doing it with FMO_STATUS_FLAGS and a restart
per attempt is what this replaces.

WHAT IT KNOWS. The gate catalogue fmodata/fmo-gates.json
(tools/fmodatagen/fmogates.py: every flag with who reads and writes it, every
event-table row with its conditions, the ladder per faction). Without it the
page falls back to fmo-missions.tsv / fmo-cutscenes.tsv, which carry the
missions and the event rows but not the scripts' own tests.

THE SPLIT. This module is pure: catalogue, evaluation, the edit operations on
a character RECORD, and the page. fmoserver/gatetool.py owns the live side --
which pilot is online, queueing an edit onto that session so the session's own
thread applies it, and the 0x015A that refreshes the client's copy of the
flags. It never imports fmoserver.
"""
import json
import os
import re
import threading
import time

try:
    import fmostore
except ImportError:                                   # pragma: no cover
    fmostore = None

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "fmodata")
GATES_JSON = os.environ.get("FMO_GATES_JSON", "").strip() or os.path.join(DATA, "fmo-gates.json")
MISSIONS_TSV = os.path.join(DATA, "fmo-missions.tsv")
CLASS_EXP_TSV = os.path.join(DATA, "fmo-class-exp.tsv")
#: the Pilot class: the event rows' and the mission catalogue's "level" is
#: this class's level (0x611782B0), not the rank byte
PILOT_CLASS = 12
CUTSCENES_TSV = os.path.join(DATA, "fmo-cutscenes.tsv")

FLAGS_LEN = 0x100
FACTIONS = {1: "O.C.U.", 2: "U.S.N."}
DONE = 99
MAX_SNAPSHOTS = 24
#: the character-record key snapshots live under (fmostore keeps unknown keys
#: in its JSON `extra` column, so this needs no schema change)
SNAP_KEY = "gate_snapshots"


# --------------------------------------------------------------------------- #
# the catalogue
# --------------------------------------------------------------------------- #
def _tsv(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        cols = f.readline().rstrip("\r\n").split("\t")
        return [dict(zip(cols, ln.rstrip("\r\n").split("\t"))) for ln in f
                if ln.strip() and not ln.startswith("#")]


def _int(v):
    try:
        return int(str(v).strip(), 0)
    except (TypeError, ValueError):
        return None


_BYTE_TEST = re.compile(r"byte\[(\d+)\]\s*(==|!=|>=|<=|>|<)\s*(\d+)")


def _rows_from_cutscenes(rows):
    """fmo-cutscenes.tsv -> event_rows. Its order within one NPC is the
    file's; its flag_bits column is kept as an unknown condition (the tsv
    does not say whether the bit must be set or clear)."""
    out, order = [], {}
    for r in rows:
        conds = []
        lo, _, hi = (r.get("rank") or "").partition("-")
        if _int(lo) is not None:
            conds.append({"type": "pilot_level", "min": _int(lo), "max": _int(hi or lo)})
        for m in _BYTE_TEST.finditer(r.get("byte_tests") or ""):
            conds.append({"type": "byte", "index": int(m.group(1)), "op": m.group(2),
                          "value": int(m.group(3))})
        for b in re.findall(r"\d+", r.get("flag_bits") or ""):
            conds.append({"type": "unknown", "raw": "bit %s" % b})
        k = (r.get("zone_table"), r.get("entity_key"))
        order[k] = order.get(k, 0) + 1
        cands = [c.strip() for c in (r.get("candidates") or "").split("|") if c.strip()]
        out.append({"faction": r.get("faction") or None, "table": r.get("zone_table"),
                    "zone_kinds": r.get("zone_kinds"), "entity_key": r.get("entity_key"),
                    "role": r.get("entry"), "order": order[k], "conditions": conds,
                    "scp": r.get("scp"), "script": r.get("script"),
                    "dialogue": r.get("dialogue"), "scene": r.get("arc") or None,
                    "mission": cands[0] if len(cands) == 1 else (" | ".join(cands) or None),
                    "confidence": r.get("confidence")})
    return out


def _missions_from_tsv(rows):
    out = {}
    for r in rows:
        fac = r.get("faction")
        if fac not in FACTIONS.values():
            continue
        out.setdefault(fac, []).append({
            "mission": r["title"], "level": _int(r.get("level")),
            "prerequisite": r.get("prerequisite") or None,
            "own_byte": _int(r.get("own_byte")), "prereq_byte": _int(r.get("prereq_byte")),
            "offered_by": r.get("client") or None, "area": r.get("area") or None,
            "confidence": r.get("own_conf") or None})
    return out


def _ladder(missions):
    """Each faction's missions in campaign order: a mission after its
    prerequisite, then by level, then by title."""
    out = {}
    for fac, ms in missions.items():
        by = {m["mission"]: m for m in ms}
        depth = {}

        def d(t, seen=()):
            if t in depth:
                return depth[t]
            m = by.get(t)
            pre = m and m["prerequisite"]
            depth[t] = 0 if not pre or pre not in by or pre in seen else d(pre, seen + (t,)) + 1
            return depth[t]
        for t in by:
            d(t)
        seq = sorted(ms, key=lambda m: ((m["level"] if m["level"] is not None else 999),
                                        depth[m["mission"]], m["mission"]))
        out[fac] = [dict(m, step=i + 1) for i, m in enumerate(seq)]
    return out


class Catalogue:
    """The gates, loaded once and again whenever a source file changes."""

    def __init__(self, gates_json=GATES_JSON, missions_tsv=MISSIONS_TSV,
                 cutscenes_tsv=CUTSCENES_TSV):
        self.paths = (gates_json, missions_tsv, cutscenes_tsv)
        self._stamp = None
        self._lock = threading.Lock()
        self.data = {}

    def _mtimes(self):
        return tuple(os.path.getmtime(p) if os.path.exists(p) else None for p in self.paths)

    def get(self):
        with self._lock:
            st = self._mtimes()
            if st != self._stamp:
                self._stamp = st
                self.data = self._load()
            return self.data

    def _load(self):
        gj, mt, ct = self.paths
        g = {}
        if os.path.exists(gj):
            try:
                with open(gj, encoding="utf-8") as f:
                    g = json.load(f)
            except (OSError, ValueError) as e:
                g = {"error": "%s: %s" % (os.path.basename(gj), e)}
        missions = _missions_from_tsv(_tsv(mt))
        ladder = g.get("ladder") or _ladder(missions)
        # every ladder step also knows its prerequisite by title, which the
        # generated ladder may name only by byte
        for fac, steps in ladder.items():
            known = {m["mission"]: m for m in missions.get(fac, [])}
            for s in steps:
                m = known.get(s.get("mission")) or {}
                for k in ("prerequisite", "level", "own_byte", "prereq_byte", "offered_by"):
                    if s.get(k) is None and m.get(k) is not None:
                        s[k] = m[k]
        rows = g.get("event_rows") or _rows_from_cutscenes(_tsv(ct))
        flags = g.get("flags") or _flags_from_ladder(ladder)
        return {"source": "catalogue" if g.get("event_rows") else "tables",
                "generated": g.get("generated"), "error": g.get("error"),
                "flags": flags, "event_rows": rows, "ladder": ladder,
                "other_gates": g.get("other_gates") or [],
                "scripts": {s.get("scp"): s for s in g.get("scripts") or []}}


def _flags_from_ladder(ladder):
    out = [{"kind": "byte", "index": 128, "name": "Pilot registered", "mission": None,
            "faction": None, "values": {"0": "not registered", "99": "registered"},
            "read_by": [], "written_by": [], "confidence": "proved"}]
    seen = {128}
    for fac, steps in ladder.items():
        for s in steps:
            b = s.get("own_byte")
            if b is None or b in seen:
                continue
            seen.add(b)
            out.append({"kind": "byte", "index": b, "name": s["mission"], "mission": s["mission"],
                        "faction": fac, "values": {"0": "not started", "99": "done"},
                        "read_by": [], "written_by": [], "confidence": s.get("confidence")})
    return sorted(out, key=lambda f: f["index"])


CATALOGUE = Catalogue()


def load_curve(path=CLASS_EXP_TSV):
    """The class exp thresholds (fmo-class-exp.tsv, D15): curve[L-1] is the
    exp level L starts at. The same file and reading as fmoserver.classes."""
    try:
        with open(path, encoding="utf-8") as fh:
            rows = [ln.rstrip("\n").split("\t") for ln in fh if ln.strip()][1:]
        curve = tuple(int(e) for _lv, e in rows)
    except (OSError, ValueError):
        return ()
    return curve if all(a <= b for a, b in zip(curve, curve[1:])) else ()


CURVE = load_curve()


def pilot_exp(char):
    ce = char.get("class_exp") or {}
    try:
        return int(ce.get(str(PILOT_CLASS), ce.get(PILOT_CLASS, 0)))
    except (TypeError, ValueError):
        return 0


def pilot_level(char, curve=None):
    """0x611E40A0 over the Pilot class's exp: 1 at the floor, else the
    largest L with curve[L-1] <= exp (fmoserver.classes.class_level)."""
    curve = CURVE if curve is None else curve
    exp = pilot_exp(char)
    if len(curve) < 2 or exp < curve[1]:
        return 1
    return max(L for L in range(1, len(curve) + 1) if curve[L - 1] <= exp)


def _set_pilot_exp(char, exp):
    ce = dict(char.get("class_exp") or {})
    ce.pop(PILOT_CLASS, None)
    ce[str(PILOT_CLASS)] = int(exp)
    char["class_exp"] = ce


def _set_pilot_level(char, level, curve):
    if not curve:
        raise ValueError("the class exp curve is missing, so a level cannot be set")
    if not 1 <= level <= len(curve):
        raise ValueError("pilot level 1-%d" % len(curve))
    _set_pilot_exp(char, curve[level - 1] if level > 1 else 0)


# --------------------------------------------------------------------------- #
# the pilot's state, and what it opens
# --------------------------------------------------------------------------- #
def flags_of(char):
    b = fmostore.flags_bytes(char.get("flags")) if fmostore else b""
    return bytearray(b.ljust(FLAGS_LEN, b"\0")[:FLAGS_LEN])


def bit(fl, i):
    return bool(fl[i >> 3] >> (i & 7) & 1) if 0 <= i < FLAGS_LEN * 8 else False


_OPS = {"==": lambda a, b: a == b, "!=": lambda a, b: a != b, ">=": lambda a, b: a >= b,
        "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, "<": lambda a, b: a < b}


def cond_holds(c, fl, who):
    """True / False, or None when the condition cannot be judged. `who` is
    {"rank": .., "level": ..} (level = Pilot level)."""
    t = c.get("type")
    if t in ("rank", "pilot_level"):
        v = who["rank" if t == "rank" else "level"]
        lo, hi = c.get("min"), c.get("max")
        return (lo is None or v >= lo) and (hi is None or v <= hi)
    if t == "byte" and _int(c.get("index")) is not None and c.get("op") in _OPS:
        return _OPS[c["op"]](fl[int(c["index"]) & 0xFF], int(c.get("value") or 0))
    if t == "bit" and _int(c.get("id")) is not None:
        return bit(fl, int(c["id"])) == bool(c.get("set", True))
    return None


def cond_text(c):
    t = c.get("type")
    if t in ("rank", "pilot_level"):
        lo, hi = c.get("min"), c.get("max")
        n = "rank" if t == "rank" else "pilot level"
        return ("%s %s-%s" % (n, lo, hi)) if hi not in (None, lo) else "%s %s" % (n, lo)
    if t == "byte":
        return "byte %s %s %s" % (c.get("index"), c.get("op"), c.get("value"))
    if t == "bit":
        return "bit %s %s" % (c.get("id"), "set" if c.get("set", True) else "clear")
    return c.get("raw") or "unknown"


def npc_outcomes(cat, fl, who, faction):
    """For every NPC in this faction's event tables: the row that plays now.

    Rows of one NPC are tried in order and the first whose conditions all
    hold is the one the client runs. A row with a condition that cannot be
    judged stops the walk as `unsure`: whether it or a later row plays depends
    on something unknown."""
    groups = {}
    for r in cat["event_rows"]:
        if r.get("faction") not in (None, faction):
            continue
        groups.setdefault((r.get("table"), r.get("entity_key")), []).append(r)
    out = []
    for (table, key), rows in groups.items():
        rows = sorted(rows, key=lambda r: r.get("order") or 0)
        fires, unsure, tried = None, False, []
        for r in rows:
            res = [cond_holds(c, fl, who) for c in r.get("conditions") or []]
            failed = [cond_text(c) for c, ok in zip(r.get("conditions") or [], res) if ok is False]
            unknown = [cond_text(c) for c, ok in zip(r.get("conditions") or [], res) if ok is None]
            tried.append({"order": r.get("order"), "scene": r.get("scene"), "scp": r.get("scp"),
                          "mission": r.get("mission"),
                          "conditions": [cond_text(c) for c in r.get("conditions") or []],
                          "failed": failed, "unknown": unknown})
            if failed:
                continue
            fires = r
            unsure = bool(unknown)
            break
        out.append({"table": table, "entity_key": key, "role": rows[0].get("role"),
                    # a story row: the one that plays is gated on something
                    "gated": bool(fires is not None and fires.get("conditions")),
                    "zone_kinds": rows[0].get("zone_kinds"),
                    "fires": None if fires is None else {
                        "order": fires.get("order"), "scene": fires.get("scene"),
                        "scp": fires.get("scp"), "mission": fires.get("mission"),
                        "dialogue": fires.get("dialogue"), "first_line": fires.get("first_line")},
                    "unsure": unsure, "rows": tried})
    return sorted(out, key=lambda o: (o["table"] or "", o["role"] or "", o["entity_key"] or ""))


def ladder_view(cat, fl, level, faction):
    steps = []
    for s in cat["ladder"].get(faction, []):
        b = s.get("own_byte")
        v = fl[b] if b is not None else None
        pre = s.get("prereq_byte")
        steps.append(dict(s, value=v,
                          state=("done" if v == DONE else "started" if v else "open")
                          if v is not None else "unknown",
                          level_ok=s.get("level") is None or level >= s["level"],
                          prereq_ok=pre is None or fl[pre] == DONE))
    return steps


def _refs(refs, n=6):
    return [" ".join(str(r.get(k) or "") for k in ("where", "ref", "test") if r.get(k))
            for r in (refs or [])[:n]]


def pilot_state(char, faction, cat=None, static=True):
    """What the page shows for one pilot. `static` adds the parts that do not
    change with the pilot's state (the other gates, who reads and writes each
    flag); the page asks for them once and keeps them."""
    cat = cat or CATALOGUE.get()
    fl = flags_of(char)
    rank = int(char.get("rank") or 0)
    level = pilot_level(char)
    who = {"rank": rank, "level": level}
    flags = []
    for f in cat["flags"]:
        i = _int(f.get("index"))
        if i is None:
            continue
        v = (fl[i] if f.get("kind") == "byte" else int(bit(fl, i)))
        row = {k: f.get(k) for k in ("kind", "index", "name", "mission", "faction",
                                     "values", "confidence")}
        row.update(value=v, read_n=len(f.get("read_by") or []),
                   write_n=len(f.get("written_by") or []))
        if static:
            row.update(reads=_refs(f.get("read_by")), writes=_refs(f.get("written_by")))
        flags.append(row)
    snaps = char.get(SNAP_KEY) or {}
    return {"rank": rank, "level": level, "exp": pilot_exp(char),
            "max_level": len(CURVE), "faction": faction,
            "raw": {str(i): fl[i] for i in range(FLAGS_LEN) if fl[i]},
            "ladder": ladder_view(cat, fl, level, faction) if faction else [],
            "npcs": npc_outcomes(cat, fl, who, faction) if faction else [],
            "flags": flags,
            "other_gates": cat["other_gates"] if static else None,
            "snapshots": sorted(({"name": k, "at": v.get("at"), "rank": v.get("rank"),
                                  "level": v.get("level")}
                                 for k, v in snaps.items()), key=lambda s: s["at"] or ""),
            "catalogue": {"source": cat["source"], "generated": cat["generated"],
                          "error": cat["error"]}}


# --------------------------------------------------------------------------- #
# edits -- on the RECORD; the caller commits and pushes
# --------------------------------------------------------------------------- #
def _set_bytes(char, values):
    fl = flags_of(char)
    for i, v in values.items():
        fl[i] = v
    char["flags"] = bytes(fl).hex()


def _prereqs(steps, title):
    """Every mission `title` depends on, transitively, in the ladder."""
    by = {s["mission"]: s for s in steps}
    out, t, seen = [], (by.get(title) or {}).get("prerequisite"), set()
    while t and t in by and t not in seen:
        seen.add(t)
        out.append(by[t])
        t = by[t].get("prerequisite")
    return out


def apply_op(char, faction, op, cat=None, curve=None):
    """Apply one edit to a character record. -> (flags_changed, [what]).
    Raises ValueError for an edit that makes no sense; changes nothing then."""
    cat = cat or CATALOGUE.get()
    curve = CURVE if curve is None else curve
    kind = op.get("op")
    before_flags, before_rank = char.get("flags"), char.get("rank")
    what = []
    if kind == "byte":
        i, v = _int(op.get("index")), _int(op.get("value"))
        if i is None or not 0 <= i < FLAGS_LEN or v is None or not 0 <= v <= 255:
            raise ValueError("byte index 0-255 and value 0-255")
        _set_bytes(char, {i: v})
        what.append("byte %d = %d" % (i, v))
    elif kind == "bit":
        i = _int(op.get("id"))
        if i is None or not 0 <= i < FLAGS_LEN * 8:
            raise ValueError("bit 0-%d" % (FLAGS_LEN * 8 - 1))
        fl = flags_of(char)
        if op.get("on"):
            fl[i >> 3] |= 1 << (i & 7)
        else:
            fl[i >> 3] &= ~(1 << (i & 7)) & 0xFF
        char["flags"] = bytes(fl).hex()
        what.append("bit %d %s" % (i, "set" if op.get("on") else "cleared"))
    elif kind == "rank":
        v = _int(op.get("value"))
        if v is None or not 0 <= v <= 255:
            raise ValueError("rank 0-255")
        char["rank"] = v
        what.append("rank = %d" % v)
    elif kind == "pilot_level":
        v = _int(op.get("value"))
        if v is None:
            raise ValueError("a pilot level is a number")
        _set_pilot_level(char, v, curve)
        what.append("pilot level = %d" % v)
    elif kind in ("ready", "done"):
        steps = cat["ladder"].get(faction or "", [])
        s = next((x for x in steps if x["mission"] == op.get("mission")), None)
        if s is None:
            raise ValueError("no such mission for this pilot's faction")
        vals = {p["own_byte"]: DONE for p in _prereqs(steps, s["mission"])
                if p.get("own_byte") is not None}
        if s.get("own_byte") is not None:
            vals[s["own_byte"]] = DONE if kind == "done" else 0
        elif kind == "done":
            raise ValueError("this mission has no known progress byte")
        lvl = s.get("level")
        if lvl is not None and pilot_level(char, curve) < lvl:
            _set_pilot_level(char, lvl, curve)
            what.append("pilot level = %d" % lvl)
        _set_bytes(char, vals)
        what.insert(0, "%s %s (%s)" % ("completed" if kind == "done" else "ready for",
                                       s["mission"], ", ".join("byte %d = %d" % kv
                                                               for kv in sorted(vals.items()))))
    elif kind == "snapshot_save":
        name = str(op.get("name") or "").strip()[:40]
        if not name:
            raise ValueError("a snapshot needs a name")
        snaps = dict(char.get(SNAP_KEY) or {})
        if name not in snaps and len(snaps) >= MAX_SNAPSHOTS:
            raise ValueError("at most %d snapshots per pilot" % MAX_SNAPSHOTS)
        snaps[name] = {"flags": bytes(flags_of(char)).hex(), "rank": int(char.get("rank") or 0),
                       "level": pilot_level(char, curve), "exp": pilot_exp(char),
                       "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        char[SNAP_KEY] = snaps
        what.append("saved snapshot %s" % name)
    elif kind == "snapshot_load":
        snap = (char.get(SNAP_KEY) or {}).get(op.get("name"))
        if not snap:
            raise ValueError("no such snapshot")
        char["flags"], char["rank"] = snap["flags"], snap["rank"]
        if snap.get("exp") is not None:
            _set_pilot_exp(char, snap["exp"])
        what.append("restored snapshot %s" % op.get("name"))
    elif kind == "snapshot_delete":
        snaps = dict(char.get(SNAP_KEY) or {})
        if snaps.pop(op.get("name"), None) is None:
            raise ValueError("no such snapshot")
        char[SNAP_KEY] = snaps
        what.append("deleted snapshot %s" % op.get("name"))
    else:
        raise ValueError("unknown edit %r" % kind)
    if before_rank != char.get("rank") and kind != "rank" and not any(w.startswith("rank") for w in what):
        what.append("rank = %s" % char.get("rank"))
    return char.get("flags") != before_flags, what


# --------------------------------------------------------------------------- #
# the page
# --------------------------------------------------------------------------- #
PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Story gates</title>
<style>
:root{
  --bg:#0f1317;--panel:#161c23;--sunk:#0b0f13;--ink:#e5eaf1;--ink2:#98a4b4;
  --ink3:#66727f;--rule:#232d38;--accent:#74a8ea;--warn:#d59450;--good:#59ab80;
  --m:ui-monospace,"IBM Plex Mono",Consolas,monospace;
}
@media (prefers-color-scheme:light){
  :root{--bg:#edeff3;--panel:#fafbfd;--sunk:#e3e7ee;--ink:#171b21;--ink2:#4b5563;
        --ink3:#7b8798;--rule:#d5dae3;--accent:#2b5ea6;--warn:#96551a;--good:#256b4c;}
}
*{box-sizing:border-box}
[hidden]{display:none!important}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:16px 14px 60px;display:grid;gap:14px;grid-template-columns:minmax(0,1fr)}
h1{font-size:1.1rem;margin:0;font-weight:600}
.card{background:var(--panel);border:1px solid var(--rule);border-radius:5px;padding:13px 15px;min-width:0}
.hd{display:flex;justify-content:space-between;align-items:baseline;gap:10px;margin-bottom:9px;flex-wrap:wrap}
.hd h2{font-size:.95rem;margin:0;font-weight:600}
.sub{font-size:12px;color:var(--ink3)}
.mono{font-family:var(--m);font-size:12.5px}
button{font:inherit;font-size:13px;cursor:pointer;color:var(--ink);background:var(--sunk);
       border:1px solid var(--rule);border-radius:4px;padding:4px 10px}
button:hover:not(:disabled){border-color:var(--accent);color:var(--accent)}
button:disabled{opacity:.4;cursor:not-allowed}
button.go{border-color:var(--good);color:var(--good)}
select,input{font:inherit;font-size:13px;background:var(--sunk);color:var(--ink);
  border:1px solid var(--rule);border-radius:4px;padding:4px 7px}
input[type=number]{width:64px}
input[type=checkbox]{accent-color:var(--accent)}
.top{display:flex;flex-wrap:wrap;gap:10px 16px;align-items:center}
.top select{min-width:0;width:min(360px,100%)}
.pill{display:inline-block;border:1px solid var(--rule);border-radius:10px;padding:0 8px;font-size:12px;color:var(--ink2)}
.pill.on{border-color:var(--good);color:var(--good)}
.pill.warn{border-color:var(--warn);color:var(--warn)}
.msg{font-size:13px;border-radius:4px;padding:6px 9px;border:1px solid var(--rule)}
.msg.ok{border-color:var(--good);color:var(--good)}
.msg.bad{border-color:var(--warn);color:var(--warn)}
.grid2{display:grid;grid-template-columns:minmax(0,1fr);gap:14px}
@media(min-width:1000px){.grid2{grid-template-columns:minmax(0,3fr) minmax(0,2fr)}}
.tw{overflow-x:auto}
table{border-collapse:collapse;width:100%}
th,td{text-align:left;padding:5px 7px;border-bottom:1px solid var(--rule);vertical-align:top}
th{font-size:11.5px;font-weight:600;color:var(--ink3);text-transform:uppercase;letter-spacing:.05em}
td.n{font-family:var(--m);font-size:12.5px;white-space:nowrap}
tr.done td{color:var(--ink2)}
.st{font-size:12px;white-space:nowrap}
.st.done{color:var(--good)} .st.started{color:var(--accent)} .st.open{color:var(--ink)}
.st.unknown{color:var(--ink3)}
.why{font-size:12px;color:var(--ink3)}
.bad{color:var(--warn)}
.acts{display:flex;gap:4px;flex-wrap:nowrap}
td.a{width:1%;white-space:nowrap}
.st{margin-right:4px}
.npc{border-bottom:1px solid var(--rule);padding:7px 0}
.npc:last-child{border-bottom:0}
.npc .l1{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap}
.npc .scene{font-weight:600}
.npc .none{color:var(--ink3)}
.npc details{margin-top:4px}
.npc summary{cursor:pointer;font-size:12px;color:var(--ink3)}
.npc ol{margin:4px 0 0;padding-left:20px;font-size:12px;color:var(--ink2)}
.npc li.hit{color:var(--good)}
.raw{display:grid;grid-template-columns:repeat(16,minmax(0,1fr));gap:2px;font-family:var(--m);font-size:11px}
.raw div{background:var(--sunk);border-radius:2px;padding:2px 0;text-align:center;color:var(--ink3);cursor:pointer}
.raw div.nz{color:var(--ink);outline:1px solid var(--accent)}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.snaps{display:grid;gap:4px;margin-top:8px}
.snaps .s{display:flex;justify-content:space-between;gap:8px;align-items:center}
.conf{font-size:11px;color:var(--ink3)}
.log{font-family:var(--m);font-size:12px;color:var(--ink2);max-height:200px;overflow:auto;white-space:pre-wrap}
</style></head><body>
<div class="wrap">
  <div class="card">
    <div class="top">
      <h1>Story gates</h1>
      <select id="pilot" aria-label="Pilot"></select>
      <span id="facts" class="sub"></span>
    </div>
    <div id="msg" class="msg" style="margin-top:10px" hidden></div>
  </div>

  <div id="none" class="card sub" hidden>No pilots on file yet.</div>

  <div id="body" hidden>
  <div class="grid2">
    <div class="card">
      <div class="hd"><h2>Campaign</h2><span class="sub" id="ladsub"></span></div>
      <div class="tw"><table><thead><tr><th>Lv</th><th>Mission</th><th>Byte</th><th>State</th><th></th></tr></thead>
      <tbody id="ladder"></tbody></table></div>
    </div>
    <div style="display:grid;gap:14px;align-content:start">
      <div class="card">
        <div class="hd"><h2>Pilot</h2></div>
        <div class="row">
          <label for="plv">Pilot level</label><input type="number" id="plv" min="1" max="100">
          <button id="setplv">Set</button>
          <label for="rank">Rank</label><input type="number" id="rank" min="0" max="255">
          <button id="setrank">Set</button>
        </div>
        <p class="sub" style="margin:6px 0 0" id="plvnote">Missions and NPC conversations are gated on Pilot level. Both reach the game at the next Start Game.</p>
      </div>
      <div class="card">
        <div class="hd"><h2>Snapshots</h2></div>
        <div class="row"><input id="snapname" placeholder="Name" maxlength="40"><button id="snapsave">Save</button></div>
        <div class="snaps" id="snaps"></div>
      </div>
      <div class="card">
        <div class="hd"><h2>Changes</h2></div>
        <div class="log" id="log"></div>
      </div>
    </div>
  </div>

  <div class="card" style="margin-top:14px">
    <div class="hd"><h2>NPCs now</h2><span class="sub">What talking to each NPC plays with this pilot's state</span></div>
    <div class="row" style="margin-bottom:6px">
      <input id="npcq" placeholder="Filter" aria-label="Filter NPCs">
      <label class="sub"><input type="checkbox" id="npcall"> Include everyday conversations</label>
    </div>
    <div id="npcs"></div>
  </div>

  <div class="card" style="margin-top:14px">
    <div class="hd"><h2>Flags</h2><span class="sub" id="flagsub"></span></div>
    <div class="row" style="margin-bottom:6px"><input id="flagq" placeholder="Filter" aria-label="Filter flags"></div>
    <div class="tw"><table><thead><tr><th>Kind</th><th>Index</th><th>Name</th><th>Value</th><th>Read / written by</th></tr></thead>
    <tbody id="flags"></tbody></table></div>
    <details style="margin-top:10px"><summary class="sub">All 256 bytes</summary>
      <div class="raw" id="raw" style="margin-top:8px"></div></details>
  </div>

  <div class="card" style="margin-top:14px" id="othercard">
    <details><summary class="hd" style="cursor:pointer"><h2>Other gates</h2><span class="sub">Pilot level, map and zone conditions</span></summary>
    <div class="tw"><table><thead><tr><th>Kind</th><th>Condition</th><th>Unlocks</th></tr></thead>
    <tbody id="other"></tbody></table></div></details>
  </div>
  </div>
</div>
<script>
const T = new URLSearchParams(location.search).get('t') || '';
const qs = p => { p = p.replace(/^\//, ''); return T ? p + (p.includes('?') ? '&' : '?') + 't=' + encodeURIComponent(T) : p; };
const q = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let S = null, PILOT = localStorage.getItem('fmoGatesPilot') || '', BUSY = false;
let LAST = '', STATIC = null;  // STATIC: {pilot, other_gates, refs by flag}

function say(text, ok){ const m = q('#msg'); m.hidden = !text; m.textContent = text || ''; m.className = 'msg ' + (ok ? 'ok' : 'bad'); }

async function load(){
  if(BUSY) return;
  try{
    const full = !STATIC || STATIC.pilot !== PILOT;
    const r = await fetch(qs('gates.json?pilot=' + encodeURIComponent(PILOT) + (full ? '&static=1' : '')));
    const t = await r.text();
    const j = JSON.parse(t);
    if(!r.ok) throw new Error(j.msg || j.error || r.statusText);
    if(t === LAST) return;
    LAST = t;
    if(j.state && j.state.other_gates){
      STATIC = {pilot: j.pilot && j.pilot.id, other_gates: j.state.other_gates, refs: {}};
      for(const f of j.state.flags || []) STATIC.refs[f.kind + f.index] = [f.reads || [], f.writes || []];
    }
    S = j; render();
  }catch(e){ say(e.message, false); }
}

async function edit(op){
  if(!S || !S.pilot) return;
  LAST = '';
  BUSY = true;
  try{
    const r = await fetch(qs('gates/edit'), {method:'POST', headers:{'Content-Type':'application/json'},
                          body: JSON.stringify(Object.assign({pilot: S.pilot.id}, op))});
    const j = await r.json();
    say(j.msg || (j.ok ? 'Done' : 'Refused'), !!j.ok);
  }catch(e){ say(e.message, false); }
  BUSY = false; load();
}

function render(){
  const ps = S.pilots || [];
  q('#none').hidden = ps.length > 0; q('#body').hidden = !S.pilot;
  const sel = q('#pilot');
  const opts = ps.map(p => `<option value="${esc(p.id)}"${S.pilot && p.id === S.pilot.id ? ' selected' : ''}>`
    + `${esc(p.name)} (${esc(p.faction || 'no faction')}, pilot level ${p.level})${p.online ? ' - online' : ''}</option>`).join('');
  if(sel.dataset.h !== opts){ sel.dataset.h = opts; sel.innerHTML = opts; }
  if(!S.pilot) return;
  const P = S.pilot, st = S.state;
  if(PILOT !== P.id) STATIC = null;
  PILOT = P.id; try{ localStorage.setItem('fmoGatesPilot', PILOT); }catch(e){}
  q('#facts').innerHTML = `<span class="pill ${P.online ? 'on' : ''}">${P.online ? 'Online' : 'Offline'}</span> `
    + (P.online ? 'Changes reach the game within seconds.' : 'Changes apply at the next login.')
    + (P.pending ? ` <span class="pill warn">${P.pending} waiting</span>` : '');
  if(document.activeElement !== q('#rank')) q('#rank').value = st.rank;
  if(document.activeElement !== q('#plv')) q('#plv').value = st.level;
  const lad = st.ladder || [];
  q('#ladsub').textContent = (st.faction || '') + ' - ' + lad.filter(s => s.state === 'done').length + ' of ' + lad.length + ' done';
  setHTML(q('#ladder'), lad.map(s => {
    const why = [];
    if(!s.level_ok) why.push('needs pilot level ' + s.level);
    if(!s.prereq_ok) why.push('needs ' + (s.prerequisite || 'byte ' + s.prereq_byte));
    const val = s.own_byte == null ? '' : `<input type="number" min="0" max="255" value="${s.value}" data-byte="${s.own_byte}" aria-label="Byte ${s.own_byte}">`;
    return `<tr class="${s.state === 'done' ? 'done' : ''}"><td class="n">${esc(s.level ?? '')}</td>`
      + `<td>${esc(s.mission)}${s.offered_by ? `<div class="why">${esc(s.offered_by)}${s.fork ? ' - ' + esc(s.fork) : ''}</div>` : ''}`
      + (why.length && s.state !== 'done' ? `<div class="why bad">${esc(why.join(', '))}</div>` : '')
      + (s.scenes || []).map(x => `<div class="why mono">${esc(x)}</div>`).join('') + `</td>`
      + `<td class="n">${s.own_byte ?? '-'}</td><td><span class="st ${s.state}">${s.state === 'unknown' ? 'no byte' : s.state}</span> ${val}</td>`
      + `<td class="a"><div class="acts"><button data-ready="${esc(s.mission)}">Ready</button>`
      + `<button data-done="${esc(s.mission)}"${s.own_byte == null ? ' disabled' : ''}>Done</button></div></td></tr>`;
  }).join('') || '<tr><td colspan="5" class="sub">No missions for this faction.</td></tr>');
  q('#snaps').innerHTML = (st.snapshots || []).map(s => `<div class="s"><span>${esc(s.name)} <span class="sub">${esc((s.at || '').replace('T', ' ').replace('Z', ''))}, pilot level ${esc(s.level ?? '?')}, rank ${esc(s.rank)}</span></span>`
    + `<span class="acts"><button data-load="${esc(s.name)}">Restore</button><button data-del="${esc(s.name)}">Delete</button></span></div>`).join('')
    || '<span class="sub">None saved.</span>';
  q('#log').textContent = (S.log || []).join('\n') || 'Nothing yet.';
  renderNpcs(); renderFlags();
  const og = (STATIC && STATIC.other_gates) || [];
  setHTML(q('#other'), og.map(g => `<tr><td class="n">${esc(g.kind)}</td><td>${esc(g.condition)}${g.name ? `<div class="why">${esc(g.name)}</div>` : ''}</td><td>${esc(g.unlocks || '')}</td></tr>`).join(''));
  q('#othercard').hidden = !og.length;
}

function renderNpcs(){
  const f = q('#npcq').value.trim().toLowerCase(), all = q('#npcall').checked;
  const list = (S.state.npcs || []).filter(n => (all ? true : n.gated) && (!f || JSON.stringify(n).toLowerCase().includes(f)));
  setHTML(q('#npcs'), list.map(n => {
    const hit = n.fires;
    const head = hit ? `<span class="scene">${esc(hit.scene || hit.mission || hit.first_line || 'Scene ' + hit.scp)}</span> <span class="mono sub">${esc(hit.scp || '')}</span>`
                     : '<span class="none">Nothing to play</span>';
    return `<div class="npc"><div class="l1"><span>${esc(n.role || n.entity_key)} <span class="mono sub">${esc(n.entity_key)} ${esc(n.table || '')}</span></span>`
      + `<span>${head}${n.unsure ? ' <span class="pill warn">depends on an unknown condition</span>' : ''}</span></div>`
      + `<details><summary>${n.rows.length} row${n.rows.length === 1 ? '' : 's'}</summary><ol>`
      + n.rows.map(r => `<li class="${hit && r.order === hit.order ? 'hit' : ''}">${esc(r.scene || r.mission || r.scp || '')} `
        + `<span class="mono">${esc(r.conditions.join(' and ') || 'always')}</span>`
        + (r.failed.length ? ` <span class="bad">fails: ${esc(r.failed.join(', '))}</span>` : '') + `</li>`).join('')
      + `</ol></details></div>`;
  }).join('') || '<div class="sub">No NPC has a story scene to play at this point.</div>');
}

function setHTML(el, html){ if(el.dataset.h !== html){ el.dataset.h = html; el.innerHTML = html; } }
function refs(x){ const r = STATIC && STATIC.refs[x.kind + x.index]; return r ? ['Read by:', ...r[0], 'Written by:', ...r[1]] : []; }

function renderFlags(){
  const f = q('#flagq').value.trim().toLowerCase();
  const fl = (S.state.flags || []).filter(x => !f || JSON.stringify(x).toLowerCase().includes(f));
  q('#flagsub').textContent = (S.state.catalogue.generated ? 'Catalogue ' + S.state.catalogue.generated.slice(0, 10) : 'From the mission tables');
  setHTML(q('#flags'), fl.map(x => {
    const vals = Object.entries(x.values || {}).map(([k, v]) => k + ' ' + v).join(', ');
    const inp = x.kind === 'bit'
      ? `<input type="checkbox" data-bit="${x.index}"${x.value ? ' checked' : ''} aria-label="Bit ${x.index}">`
      : `<input type="number" min="0" max="255" value="${x.value}" data-byte="${x.index}" aria-label="Byte ${x.index}">`;
    return `<tr><td class="n">${esc(x.kind)}</td><td class="n">${x.index}</td>`
      + `<td>${esc(x.name || '')}${vals ? `<div class="why">${esc(vals)}</div>` : ''}${x.confidence ? `<div class="conf">${esc(x.confidence)}</div>` : ''}</td>`
      + `<td>${inp}</td><td class="n" title="${esc(refs(x).join('\n'))}">${x.read_n} / ${x.write_n}</td></tr>`;
  }).join(''));
  const raw = S.state.raw || {};
  setHTML(q('#raw'), Array.from({length: 256}, (_, i) => `<div class="${raw[i] ? 'nz' : ''}" data-rawi="${i}" title="Byte ${i}">${(raw[i] || 0).toString(16).padStart(2, '0')}</div>`).join(''));
}

q('#pilot').addEventListener('change', e => { PILOT = e.target.value; load(); });
q('#setrank').addEventListener('click', () => edit({op: 'rank', value: +q('#rank').value}));
q('#setplv').addEventListener('click', () => edit({op: 'pilot_level', value: +q('#plv').value}));
q('#snapsave').addEventListener('click', () => { const n = q('#snapname').value.trim(); if(n){ edit({op: 'snapshot_save', name: n}); q('#snapname').value = ''; } });
q('#npcq').addEventListener('input', renderNpcs); q('#npcall').addEventListener('change', renderNpcs);
q('#flagq').addEventListener('input', renderFlags);
document.addEventListener('click', e => {
  const b = e.target.closest('button[data-ready],button[data-done],button[data-load],button[data-del]');
  if(b){
    if(b.dataset.ready) edit({op: 'ready', mission: b.dataset.ready});
    else if(b.dataset.done) edit({op: 'done', mission: b.dataset.done});
    else if(b.dataset.load) edit({op: 'snapshot_load', name: b.dataset.load});
    else if(b.dataset.del && confirm('Delete snapshot ' + b.dataset.del + '?')) edit({op: 'snapshot_delete', name: b.dataset.del});
    return;
  }
  const c = e.target.closest('[data-rawi]');
  if(c){ const v = prompt('Byte ' + c.dataset.rawi + ' (0-255)', parseInt(c.textContent, 16)); if(v !== null && v !== '') edit({op: 'byte', index: +c.dataset.rawi, value: +v}); }
});
document.addEventListener('change', e => {
  const t = e.target;
  if(t.dataset.byte !== undefined) edit({op: 'byte', index: +t.dataset.byte, value: +t.value});
  else if(t.dataset.bit !== undefined) edit({op: 'bit', id: +t.dataset.bit, on: t.checked});
});
load(); setInterval(() => { if(!document.hidden && !document.activeElement.matches('input')) load(); }, 4000);
</script></body></html>
"""


# --------------------------------------------------------------------------- #
# selftest
# --------------------------------------------------------------------------- #
def selftest():
    import tempfile
    bad = []

    def check(label, cond):
        print(("  ok   " if cond else "  FAIL ") + label)
        if not cond:
            bad.append(label)

    tmp = tempfile.mkdtemp(prefix="fmogates-")
    mt = os.path.join(tmp, "m.tsv")
    ct = os.path.join(tmp, "c.tsv")
    with open(mt, "w", encoding="utf-8") as f:
        f.write("faction\ttitle\tlevel\tprerequisite\town_byte\tprereq_byte\town_conf\tclient\n"
                "O.C.U.\tFirst\t3\t\t130\t\ttable\tSon\n"
                "O.C.U.\tSecond\t9\tFirst\t137\t130\ttable\tSon\n"
                "O.C.U.\tThird\t13\tSecond\t\t137\t\tFuller\n")
    with open(ct, "w", encoding="utf-8") as f:
        f.write("faction\tzone_table\tzone_kinds\tentity_key\tentry\trank\tflag_bits\tbyte_tests\tscp\tscript\tdialogue\tarc\tcandidates\tconfidence\n"
                "O.C.U.\tD39\t100\t0x1\tson\t9-50\t\tbyte[130]==99 & byte[137]==0\t0x80b2\ts\td\tBriefing two\tSecond\ttable\n"
                "O.C.U.\tD39\t100\t0x1\tson\t3-50\t\tbyte[130]==0\t0x80b0\ts\td\tBriefing one\tFirst\ttable\n"
                "O.C.U.\tD39\t100\t0x2\tnina\t1-50\t120\t\t0x8010\ts\td\tMystery\t\tinferred\n")
    global CURVE
    saved_curve, CURVE = CURVE, tuple(i * 1000 for i in range(60))
    cat = Catalogue(os.path.join(tmp, "absent.json"), mt, ct).get()
    check("falls back to the tables without a catalogue", cat["source"] == "tables")
    lad = cat["ladder"]["O.C.U."]
    check("ladder is in campaign order", [s["mission"] for s in lad] == ["First", "Second", "Third"])
    char = {"rank": 1, "flags": ""}
    st = pilot_state(char, "O.C.U.", cat)
    son = next(n for n in st["npcs"] if n["entity_key"] == "0x1")
    check("pilot level 1: Son has nothing (both rows fail on level)", son["fires"] is None)
    char["rank"] = 40
    check("rank does not open a level window",
          next(n for n in pilot_state(char, "O.C.U.", cat)["npcs"] if n["entity_key"] == "0x1")["fires"] is None)
    fc, what = apply_op(char, "O.C.U.", {"op": "ready", "mission": "Second"}, cat)
    fl = flags_of(char)
    check("ready Second: First done, Second open, pilot level raised, rank untouched",
          fc and fl[130] == 99 and fl[137] == 0 and pilot_level(char) == 9 and char["rank"] == 40)
    st = pilot_state(char, "O.C.U.", cat)
    son = next(n for n in st["npcs"] if n["entity_key"] == "0x1")
    check("then Son plays Briefing two (the first row that holds)",
          son["fires"] and son["fires"]["scene"] == "Briefing two")
    nina = next(n for n in st["npcs"] if n["entity_key"] == "0x2")
    check("an unjudgeable bit makes the outcome unsure", nina["unsure"])
    apply_op(char, "O.C.U.", {"op": "snapshot_save", "name": "before"}, cat)
    apply_op(char, "O.C.U.", {"op": "done", "mission": "Second"}, cat)
    check("done Second sets its byte to 99", flags_of(char)[137] == 99)
    apply_op(char, "O.C.U.", {"op": "snapshot_load", "name": "before"}, cat)
    apply_op(char, "O.C.U.", {"op": "pilot_level", "value": 30}, cat)
    check("pilot level is set through the Pilot class exp", pilot_level(char) == 30
          and char["class_exp"]["12"] == 29000)
    apply_op(char, "O.C.U.", {"op": "snapshot_load", "name": "before"}, cat)
    check("a snapshot restores the flags, rank and pilot level",
          flags_of(char)[137] == 0 and char["rank"] == 40 and pilot_level(char) == 9)
    apply_op(char, "O.C.U.", {"op": "bit", "id": 9, "on": True}, cat)
    check("a bit sets inside its byte", flags_of(char)[1] == 2)
    apply_op(char, "O.C.U.", {"op": "bit", "id": 9, "on": False}, cat)
    check("and clears", flags_of(char)[1] == 0)
    for op in ({"op": "byte", "index": 300, "value": 1}, {"op": "done", "mission": "Third"},
               {"op": "ready", "mission": "Nope"}, {"op": "explode"}):
        try:
            apply_op(char, "O.C.U.", op, cat)
            check("refuses %s" % op, False)
        except ValueError:
            check("refuses %s" % json.dumps(op), True)
    gj = os.path.join(tmp, "g.json")
    with open(gj, "w", encoding="utf-8") as f:
        json.dump({"generated": "2026-09-30", "flags": [{"kind": "bit", "index": 120, "name": "x"}],
                   "event_rows": [{"faction": "O.C.U.", "table": "D39", "entity_key": "0x2", "order": 1,
                                   "conditions": [{"type": "bit", "id": 120, "set": True}],
                                   "scp": "0x8010", "scene": "Mystery"}]}, f)
    cat2 = Catalogue(gj, mt, ct).get()
    char2 = {"rank": 1, "flags": ""}
    apply_op(char2, "O.C.U.", {"op": "bit", "id": 120, "on": True}, cat2)
    n2 = pilot_state(char2, "O.C.U.", cat2)["npcs"][0]
    check("the catalogue's bit condition is judged", n2["fires"] and not n2["unsure"])
    check("the catalogue keeps the tables' ladder when it has none", cat2["ladder"]["O.C.U."][0]["mission"] == "First")
    CURVE = saved_curve
    print("all checks passed" if not bad else "%d check(s) FAILED" % len(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    print(__doc__)
