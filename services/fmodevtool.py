#!/usr/bin/env python3
"""fmodevtool.py -- FMO's lobby NPC editor: a floor plan, the cast, drag to
place, drag to turn. Rides fedevtool's server (gate, /state, /edit) with its
own page.

    python fmodevtool.py --selftest
    python fmodevtool.py --serve 8798        # stand-alone, prod's HQ roster,
                                             # a temp layout -- to look at it

WHAT IT IS FOR. Placing a counter NPC in an FMO lobby meant reading a
position off the client's cmd-240 stream, editing one env string on prod
and hand-recreating the container. Fantasy Earth's world-building panel
showed the better shape -- a map, the roster, click to place -- and once it
stopped needing a game session it was clearly the right shape for FMO too.

WHAT IS DIFFERENT FROM FE'S. FMO has no minimap art. Its map containers do
carry every prop's world-space box (fmolayout / fmodata/floorplans), so the
page draws those top-down and that IS the plan: the console platforms, the
counter wall, the doors. The box under a click is the floor height. And the
cast is not one roster per area but one per zone BAND (HQ, occupation,
frontline, coliseum), all on the map FMO_ZONE_MAPNO serves that band on;
the U.S.N. halves are derived by SE's own pairing and are not edited here.

WHAT AN EDIT DOES. It writes the layout file (fmolayout.Layout) on this
thread and answers with the result. The world channel re-reads the file on
its mtime and pops from it on the next NPC re-pop (FMO_UDP_POP_NPC_REPOP),
so a moved person shows within that interval to anyone in the lobby -- a
present key takes the client's update arm (visual rebuild, the blink the
REPOP knob already costs). A REMOVED person stays until the player re-enters:
the lobby session ignores cmd 8 (fmoworld.record_depop), and the page says so.

TERMINALS (2026-09-11). A 4gamer press shot of the Feb-2005 PC lobby
shows the console island with two floating
labels, `MAP.SELECTOR` and `SCRAMBLE.BOARD`, and NO human under either -- the
`name1.name2` tag of an entity drawn at the console. SE's own guide says "stand
in front of the Map Selector and press the button". So the original popped the
terminals as BODILESS named entities. The client's create dispatch has exactly
one unit type that is neither the human (4) nor the wanzer classes (0-3/5/6):
UnitType 30, its own arm at 0x611EA980, unread. The editor's "terminal" body
pops a row as that type with no catalogue dress, label uppercased the way SE
wrote it. WARNING: UNTESTED LIVE: whether 30 draws a label with no body, draws
nothing at all, or is refused, is the experiment. One row, then look.

THIS MODULE NEVER IMPORTS fmo.py. fmo.py builds a context (what each band is
served with, who is in world, the facing sign) and hands it over; this owns
the page and the edit rules, nothing else -- so it runs on its own for the
browser test and the stand-alone preview.
"""
import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import fmolayout  # noqa: E402

try:
    import fmoworld
except Exception:                                      # pragma: no cover
    fmoworld = None

_LOCK = threading.Lock()
#: the last edits and their answers, for the page: fedevtool's log tail is
#: shared with the whole server log and 300 lines of NPC re-pop chatter push
#: an edit's verdict out within a minute (live 2026-09-11: "it won't let me
#: place the first one" and no trace of why)
EDITS = []
#: the unit type the editor pops a "terminal" as -- the client's third create
#: arm (0x611EA980), see the module docstring. A knob for the day the arm is read.
TERMINAL_TYPE = int(os.environ.get("FMO_TERMINAL_UNITTYPE", "").strip() or "30", 0)


# --------------------------------------------------------------------------- #
# the roster as the page sees it
# --------------------------------------------------------------------------- #
def person(cat):
    """The catalogue person for a typecode: {"cat", "name", "face", "uniform"}."""
    if cat is None or fmoworld is None or cat not in fmoworld.NPC_CATALOGUE:
        return {"cat": cat, "name": "typecode %s" % cat if cat is not None
                else "the player's own body (no #typecode)",
                "face": None, "uniform": None}
    n1, n2, _sel, face, uniform, _size, _build, _w40 = fmoworld.NPC_CATALOGUE[cat]
    return {"cat": cat, "name": "%s %s" % (n1, n2), "face": face, "uniform": uniform}


def people_for(band):
    lo, hi = next(b[3] for b in fmolayout.BANDS if b[0] == band)
    out = []
    if fmoworld is None:
        return out
    for cat in sorted(fmoworld.NPC_CATALOGUE):
        if lo <= cat <= hi:
            out.append(person(cat))
    return out


def roles_for(band):
    return [{"key": k, "keyhex": "0x%08x" % k, "label": lab,
             "terminal": fmolayout.TERMINAL_LABELS.get(k)}
            for k, lab, bands in fmolayout.ROLES if band in bands]


def row_view(i, r):
    p = person(r.get("cat"))
    ty = r.get("type", 4)
    return {"idx": i, "key": r["key"], "keyhex": "0x%08x" % r["key"],
            "label": r.get("label") or "", "cat": r.get("cat"),
            "person": ("terminal: the machine its key names (UnitType %d)" % ty) if ty != 4 else p["name"],
            "type": ty, "terminal": ty != 4, "ckind": r.get("ckind"),
            "x": r["x"], "y": r["y"], "z": r["z"], "face": r.get("face"),
            "name": (r.get("label") or p["name"]).replace(".", " ")}


def band_info(band):
    return fmolayout.band_info(band)


def effective_floor(ctx, mapno, floor_y):
    """The map's real floor: where most walked readings stand, else the
    served spawn height. 141's spawn is 0.50 and every reading is 0.00."""
    w = ctx.get("walked")
    if w is not None and mapno is not None:
        m = w.floor_mode(mapno)
        if m is not None:
            return m
    return floor_y


def band_of_zone(zone):
    """The lobby band a zone id (e.g. 200) belongs to, by its kind, or None."""
    if zone is None:
        return None
    kind = int(zone) // 100
    return next((b[0] for b in fmolayout.BANDS if kind in b[2]), None)


# --------------------------------------------------------------------------- #
# state
# --------------------------------------------------------------------------- #
def build_state(ctx, band=None, want_plan=False):
    """The page's state. `ctx` (from fmo.py, or the selftest):
        {"layout": fmolayout.Layout,
         "served": {band: {"mapno", "floor_y", "roster", "names", "source"}},
         "live": [{"host", "mapno", "zone", "x", "y", "z", "rot"}],
         "face_sign": int}
    `band` is the one on screen (default: the first); `want_plan` includes the
    floor plan (tens of KB -- the page asks once per map, not per poll)."""
    L = ctx["layout"]
    served = ctx.get("served") or {}
    sign = ctx.get("face_sign", -1)
    band = band if band in fmolayout.BAND_IDS else fmolayout.BAND_IDS[0]
    bid, title, kinds, catrange, desc, place_kind = band_info(band)
    sv = served.get(band) or {}
    mapno = sv.get("mapno")
    floor_y = effective_floor(ctx, mapno, sv.get("floor_y"))
    lay = L.band(band)
    in_layout = lay is not None
    if in_layout and lay.get("mapno") and mapno is None:
        mapno = lay["mapno"]
    rows = lay["npcs"] if in_layout else []
    served_rows = fmolayout.roster_to_rows(sv.get("roster") or [],
                                           sv.get("names") or {}, sign)
    st = {
        "band": band, "title": title, "desc": desc, "kinds": list(kinds),
        "place_kind": place_kind,
        "mapno": mapno, "floor_y": floor_y,
        "bands": [{"id": b[0], "title": b[1], "mapno": (served.get(b[0]) or {}).get("mapno"),
                   "place": bool(b[5]), "in_layout": L.has(b[0]),
                   "n": len(L.band(b[0])["npcs"]) if L.has(b[0])
                        else len((served.get(b[0]) or {}).get("roster") or [])}
                  for b in fmolayout.BANDS],
        "in_layout": in_layout,
        "layout_mapno": lay.get("mapno") if in_layout else None,
        "npcs": [row_view(i, r) for i, r in enumerate(rows)],
        "served": {"n": len(served_rows), "source": sv.get("source") or "nothing served",
                   "rows": [row_view(i, r) for i, r in enumerate(served_rows)]},
        "spec": fmolayout.to_spec(rows, sign) if in_layout else "",
        "roles": roles_for(band), "people": people_for(band),
        # a player is "here" when they are in THIS band's zone kind (two lobby
        # bands share map 102, so the map alone said "here" for the wrong tab
        # -- live 2026-09-11: HQ edits never reached a player in zone 200);
        # place bands fall back to the map
        "live": [dict(p, band=band_of_zone(p.get("zone")),
                      here=((band_of_zone(p.get("zone")) == band) if not place_kind and p.get("zone") is not None
                            else (p.get("mapno") == mapno)))
                 for p in (ctx.get("live") or [])],
        "layout_path": L.path, "face_sign": sign,
        "edits": list(reversed(EDITS[-12:])),
        # the hangar's gate (fmo.HANGAR_REQUIRED_KEYS / 0x61002BC0): the owner's
        # SETUP.CONSOLE and PILOT.LOCKER open only when all three are placed
        "required": ([{"keyhex": "0x%08x" % k, "label": fmolayout.TERMINAL_LABELS.get(k),
                       "placed": any(r["key"] == k for r in rows)}
                      for k in (0x82080D00, 0x82080C00, 0x82080C01)] if band == "hangar" else []),
        "plans": fmolayout.plans_available(),
        "has_plan": mapno is not None and fmolayout.load_plan(mapno) is not None,
        # how many readings the walked-ground overlay holds for this map: the
        # page refetches the overlay bundle when this moves (someone is walking)
        "walked_n": (ctx["walked"].count(mapno)
                     if ctx.get("walked") is not None and mapno is not None else 0),
        "levels": (ctx["walked"].levels(mapno)
                   if ctx.get("walked") is not None and mapno is not None else []),
    }
    if want_plan and mapno is not None:
        plan = fmolayout.load_plan(mapno)
        if plan:
            st["plan"] = {"mapno": mapno, "boxes": plan["boxes"],
                          "source": plan.get("source"),
                          "extent": fmolayout.focus_extent(mapno, floor_y if floor_y is not None else 0.0, plan),
                          "walked_n": st["walked_n"]}
        # the three overlays (2026-09-11, a tester's verdict on the first
        # version: "those maps are super unhelpful"): SE's own script marks
        # for the lobby the band runs,
        # every cell a real player has stood in, and the boxes classified
        # by shape on the page. Cheap, true, and each toggles.
        st["marks"] = fmolayout.load_marks(band) if not place_kind else []
        w = ctx.get("walked")
        st["walked"] = {"cell": fmolayout.WalkedGround.CELL,
                        "cells": w.cells(mapno) if w is not None else []}
    return st


# --------------------------------------------------------------------------- #
# edits
# --------------------------------------------------------------------------- #
def apply_edit(ctx, op):
    """One editor change -> {"ok", "msg"}. Writes the layout file. Every
    verdict is kept in EDITS (last 40) for the page."""
    res = _apply_edit(ctx, op)
    with _LOCK:
        EDITS.append({"t": time.strftime("%H:%M:%S", time.gmtime()), "op": op.get("op"),
                      "band": op.get("band"), "ok": bool(res.get("ok")), "msg": res.get("msg", "")})
        del EDITS[:-40]
    return res


def _apply_edit(ctx, op):
    L = ctx["layout"]
    served = ctx.get("served") or {}
    sign = ctx.get("face_sign", -1)
    kind = str(op.get("op") or "")

    def fail(msg):
        return {"ok": False, "msg": msg}

    def num(k):
        try:
            return float(op[k])
        except (KeyError, TypeError, ValueError):
            raise ValueError(k)
    try:
        band = str(op.get("band") or "")
        if band not in fmolayout.BAND_IDS:
            return fail("no band %r" % band)
        sv = served.get(band) or {}
        lay = L.band(band)
        mapno = (lay or {}).get("mapno") or sv.get("mapno")
        floor_y = effective_floor(ctx, mapno, sv.get("floor_y"))
        if kind == "import":
            rows = fmolayout.roster_to_rows(sv.get("roster") or [], sv.get("names") or {}, sign)
            if mapno is None:
                return fail("the server serves no map for this band, nothing to import")
            with _LOCK:
                L.set_band(band, mapno, rows)
            return {"ok": True, "msg": "imported %d served NPC(s) into the layout for "
                    "the %s band on map %d -- the file is what is served now"
                    % (len(rows), band, mapno)}
        if kind == "drop_band":
            with _LOCK:
                n = L.drop_band(band)
            return {"ok": True, "msg": ("the %s band is back on the env roster" % band)
                    if n else "the layout had no %s band" % band}
        if kind in ("place", "move"):
            if mapno is None:
                return fail("no map is served for this band -- set FMO_ZONE_MAPNO")
            if floor_y is None:
                return fail("no floor height is known for map %s -- add it to "
                            "FMO_UDP_POP_POS_MAP (a measured standing position)" % mapno)
            x, z = num("x"), num("z")
            w = ctx.get("walked")
            wy = w.ground_at(mapno, x, z) if w is not None else None
            levels = w.levels(mapno) if w is not None else []
            g = None if wy is not None else fmolayout.ground_at(mapno, x, z, floor_y, levels=levels)
            ny = None
            if wy is None and g is None and w is not None:
                ny = w.near_level(mapno, x, z)
            if wy is not None:
                # a cell somebody actually stood in is ground by definition
                y, how = wy, "from walked ground (a player stood here)"
            elif g is not None:
                y, box = g
                how = "from a %.1fx%.1f box%s" % (box[3] - box[0], box[5] - box[2],
                                                  " at a height players stand at" if levels else "")
            elif ny is not None:
                y, how = ny, "from the walked floor nearby (within 8 m)"
            else:
                if False:
                    pass
                else:
                    # neither a box nor a walked cell: the rooms and hangars
                    # have unboxed floors (map 124's spawn, map 141's bays) and
                    # nobody has walked most of them yet. A wrong height on an
                    # NPC is merely wrong -- it cannot hang the client the way
                    # a bad door landing did in FE -- so take the map's known
                    # standing height (FMO_UDP_POP_POS_MAP's y) and SAY it is
                    # a guess. Walk there once and the next move corrects it.
                    y, how = round(float(floor_y), 2), ("floor GUESSED from the map's known "
                                                        "standing height -- no box and nobody "
                                                        "has walked here; walk past it once and "
                                                        "re-drop it to get the real floor")
        rows = list((lay or {}).get("npcs") or [])
        # KEY: A TERMINAL SNAPS ONTO THE MAP'S OWN PROP (fmolayout.prop_near): the
        # machine is already in the map; the type-30 entity draws the same
        # model by its key, so SE put it exactly there. Only for a terminal
        # body, only when asked (the page sends snap on by default), within
        # 1.5 m of the click.
        def _snap(x, z, is_terminal):
            if not is_terminal or op.get("snap") in (False, 0, "0", "false"):
                return x, None, z, ""
            p = fmolayout.prop_near(mapno, x, z)
            if p is None:
                return x, None, z, ""
            cx, by, cz, b = p
            return cx, by, cz, (" -- SNAPPED onto the map's %.1fx%.1fx%.1f prop at (%.2f, %.2f)"
                                % (b[3] - b[0], b[4] - b[1], b[5] - b[2], cx, cz))
        if kind == "place":
            try:
                key = int(str(op.get("key")), 0)
            except (TypeError, ValueError):
                return fail("entity key %r is not a number" % op.get("key"))
            cat = op.get("cat")
            cat = None if cat in (None, "", "none") else int(cat)
            utype = TERMINAL_TYPE if op.get("body") == "terminal" else int(op.get("type") or 4)
            if utype != 4:
                cat = None                       # a catalogue dress is a HUMAN's
            if cat is not None and fmoworld is not None and cat not in fmoworld.NPC_CATALOGUE:
                return fail("typecode %d is not in the catalogue" % cat)
            if any(r["key"] == key for r in rows):
                return fail("0x%08x is already placed in this band -- move that one, "
                            "or remove it first (one body per key)" % key)
            face = op.get("face")
            label = op.get("label") or None
            if utype != 4 and label:
                label = label.upper()            # MAP.SELECTOR, as SE drew it
            x, sy, z, snapped = _snap(x, z, utype != 4)
            if sy is not None:
                y, how = sy, "the prop's own base"
            row = fmolayout.validate_row({
                "key": key, "type": utype, "x": x, "y": y, "z": z,
                "face": None if face in (None, "") else float(face),
                "cat": cat, "label": label})
            rows.append(row)
            with _LOCK:
                L.set_band(band, mapno, rows)
            return {"ok": True, "msg": "placed %s (0x%08x)%s at (%.2f, %.2f), floor %.2f %s%s"
                    % (row_view(0, row)["name"], key,
                       " as a TERMINAL (UnitType %d: it draws the model its KEY names -- the machine itself)" % utype if utype != 4 else "",
                       x, z, y, how,
                       snapped + (" -- shows on the next re-pop" if sv.get("roster") is not None else ""))}
        if kind in ("move", "turn", "remove", "relabel", "recast", "rekey", "body", "ckind"):
            if lay is None:
                return fail("the %s band is not in the layout yet -- import the "
                            "served roster first, or place something" % band)
            idx = int(op["idx"])
            if not 0 <= idx < len(rows):
                return fail("no NPC row %d in the %s band" % (idx, band))
            who = row_view(idx, rows[idx])["name"]
            if kind == "move":
                x, sy, z, snapped = _snap(x, z, rows[idx].get("type", 4) != 4)
                if sy is not None:
                    y, how = sy, "the prop's own base"
                rows[idx].update({"x": round(x, 2), "y": y, "z": round(z, 2)})
                msg = "moved %s to (%.2f, %.2f), floor %.2f %s%s" % (who, x, z, y, how, snapped)
            elif kind == "turn":
                face = op.get("face")
                rows[idx]["face"] = None if face in (None, "") else float(face) % 360.0
                msg = ("%s now faces %g deg" % (who, rows[idx]["face"])
                       if rows[idx]["face"] is not None else "%s has no facing" % who)
            elif kind == "remove":
                rows.pop(idx)
                msg = ("removed %s -- it leaves the served roster now, but a body "
                       "already on screen stays until that player re-enters "
                       "(the lobby session ignores cmd 8)" % who)
            elif kind == "relabel":
                rows[idx]["label"] = op.get("label") or None
                msg = "%s is labelled %r" % (who, rows[idx]["label"])
            elif kind == "ckind":
                ck = op.get("ckind")
                rows[idx]["ckind"] = None if ck in (None, "", "default") else int(ck)
                msg = ("%s pops with client_kind %s -- untested: does a NAME TAG appear over it?"
                       % (who, rows[idx]["ckind"]) if rows[idx]["ckind"] is not None
                       else "%s pops with the server's default client_kind again" % who)
            elif kind == "body":
                if op.get("body") == "terminal":
                    rows[idx]["type"], rows[idx]["cat"] = TERMINAL_TYPE, None
                    if rows[idx].get("label"):
                        rows[idx]["label"] = rows[idx]["label"].upper()
                    msg = ("%s is now a TERMINAL: UnitType %d, no catalogue body -- untested, "
                           "look for a floating label at the console" % (who, TERMINAL_TYPE))
                else:
                    rows[idx]["type"] = 4
                    msg = "%s is a person again (UnitType 4; pick a catalogue body with recast)" % who
            elif kind == "recast":
                cat = op.get("cat")
                cat = None if cat in (None, "", "none") else int(cat)
                if cat is not None and fmoworld is not None and cat not in fmoworld.NPC_CATALOGUE:
                    return fail("typecode %d is not in the catalogue" % cat)
                rows[idx]["cat"] = cat
                msg = "%s is now %s" % (who, person(cat)["name"])
            else:
                key = int(str(op.get("key")), 0)
                if any(i != idx and r["key"] == key for i, r in enumerate(rows)):
                    return fail("0x%08x is already placed in this band" % key)
                rows[idx]["key"] = key
                msg = "%s is now entity 0x%08x" % (who, key)
            with _LOCK:
                L.set_band(band, mapno, rows)
            return {"ok": True, "msg": msg}
        return fail("unknown edit %r" % kind)
    except ValueError as e:
        return fail("missing or bad field %s" % e)
    except (KeyError, TypeError) as e:
        return fail("missing field %s" % e)


# --------------------------------------------------------------------------- #
# the page
# --------------------------------------------------------------------------- #
PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>fmo lobby editor</title>
<style>
:root{
  --bg:#0f1317;--panel:#161c23;--sunk:#0b0f13;--ink:#e5eaf1;--ink2:#98a4b4;
  --ink3:#66727f;--rule:#232d38;--accent:#74a8ea;--warn:#d59450;--good:#59ab80;
  --floor:#1f2a35;--floor2:#2a3847;--furn:#3b4d61;--high:#4a5a6c;
  --m:ui-monospace,"IBM Plex Mono",Consolas,monospace;
}
@media (prefers-color-scheme:light){
  :root{--bg:#edeff3;--panel:#fafbfd;--sunk:#e3e7ee;--ink:#171b21;--ink2:#4b5563;
        --ink3:#7b8798;--rule:#d5dae3;--accent:#2b5ea6;--warn:#96551a;--good:#256b4c;
        --floor:#d9dfe8;--floor2:#c7d0dc;--furn:#a9b6c6;--high:#8d9bad;}
}
*{box-sizing:border-box}
[hidden]{display:none!important}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,sans-serif}
.wrap{max-width:1360px;margin:0 auto;padding:18px 14px 60px;display:grid;gap:14px}
.wrap>*{width:100%;max-width:900px;justify-self:center}
.wrap>#mapcard{max-width:none}
h1{font-size:1.15rem;margin:0;font-weight:600;letter-spacing:-.01em}
h1 small{color:var(--ink3);font-weight:400;font-family:var(--m);font-size:11px;
         letter-spacing:.12em;text-transform:uppercase;display:block;margin-bottom:4px}
.card{background:var(--panel);border:1px solid var(--rule);border-radius:5px;padding:13px 15px}
.hd{display:flex;justify-content:space-between;align-items:baseline;gap:10px;margin-bottom:9px}
.hd h2{font-size:.95rem;margin:0;font-weight:600}
.hd .sub{font-family:var(--m);font-size:11.5px;color:var(--ink3)}
button{font:inherit;font-size:13.5px;cursor:pointer;color:var(--ink);
       background:var(--sunk);border:1px solid var(--rule);border-radius:4px;padding:6px 12px}
button:hover:not(:disabled){border-color:var(--accent);color:var(--accent)}
button:disabled{opacity:.4;cursor:not-allowed}
button.go{border-color:var(--good);color:var(--good)}
button.warn{border-color:var(--warn);color:var(--warn)}
button.on{background:var(--accent);border-color:var(--accent);color:var(--bg)}
/* hover paints text ACCENT, and the selected tab's background IS accent: the
   band you just clicked read as an empty blue box while the mouse rested on it */
button.on:hover:not(:disabled){color:var(--bg);border-color:var(--ink)}
select,input:not([type]),input[type=text],textarea{font:inherit;font-size:13px;background:var(--sunk);
  color:var(--ink);border:1px solid var(--rule);border-radius:4px;padding:5px 7px}
input[type=checkbox]{margin:0;accent-color:var(--accent)}
input[type=range]{accent-color:var(--accent)}
.maprow{display:grid;grid-template-columns:1fr;gap:14px}
@media(min-width:1000px){.maprow{grid-template-columns:minmax(0,max(320px,calc(100vh - 150px))) 380px;justify-content:start}}
.mapcol{display:grid;gap:8px;align-content:start;min-width:0}
.toolbar{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center}
.bandtabs{display:flex;flex-wrap:wrap;gap:6px}
.bandtabs button{padding:4px 11px;font-size:13px}
.chips{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:12.5px;color:var(--ink2)}
.chips label{display:inline-flex;gap:5px;align-items:center;cursor:pointer;white-space:nowrap}
.mapwrap{position:relative;width:100%;max-width:max(320px,calc(100vh - 150px));aspect-ratio:1/1;
         background:var(--sunk);border:1px solid var(--rule);border-radius:4px;overflow:hidden}
.mapwrap canvas{position:absolute;inset:0;width:100%;height:100%}
.mapfoot{display:flex;flex-wrap:wrap;gap:4px 14px;align-items:center}
.key{display:inline-flex;gap:6px;align-items:center;color:var(--ink2);font-size:12px}
.key i{width:10px;height:10px;border-radius:2px;border:1px solid #0006;flex:0 0 auto}
.key i.r{border-radius:50%}
.readout{font-family:var(--m);font-size:12px;color:var(--ink2);background:var(--sunk);
         border-radius:3px;padding:5px 8px;min-height:24px}
.editmsg{font-size:12.5px;border-radius:4px;padding:6px 9px;border:1px solid var(--rule)}
.editmsg.ok{border-color:var(--good);color:var(--good)}
.editmsg.bad{border-color:var(--warn);color:var(--warn)}
.side{display:grid;gap:10px;align-content:start;min-width:0}
.side h3{font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--ink3);
         margin:0;font-weight:600;display:flex;justify-content:space-between}
.npclist{display:grid;gap:3px;max-height:300px;overflow:auto;padding-right:2px}
.npclist button{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;align-items:center;
  text-align:left;padding:5px 8px;font-size:12.5px}
.npclist button.on{border-color:var(--warn);background:color-mix(in srgb,var(--warn) 14%,var(--sunk))}
.npclist .k{font-family:var(--m);color:var(--ink3);font-size:11px}
.detail{border:1px solid var(--warn);border-radius:5px;padding:10px 11px;display:grid;gap:7px;font-size:13px}
.detail .t{font-weight:600}
.detail dl{display:grid;grid-template-columns:auto minmax(0,1fr);gap:3px 10px;margin:0}
.detail dt{color:var(--ink3);font-size:12px}
.detail dd{margin:0;font-family:var(--m);font-size:12.5px}
.detail .acts{display:flex;flex-wrap:wrap;gap:6px}
.detail .acts button{padding:4px 9px;font-size:12.5px}
.detail .row2{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:6px}
.why{font-size:12px;color:var(--ink3);margin:0}
.placer{display:grid;gap:6px;border:1px solid var(--rule);border-radius:5px;padding:9px}
.placer .at{font-family:var(--m);font-size:12px;color:var(--accent)}
.placer select,.placer input{width:100%}
.placer .row2{display:grid;grid-template-columns:1fr auto;gap:6px}
.livebar{font-size:12px;color:var(--ink3);border-left:3px solid var(--rule);padding:2px 0 2px 9px}
.livebar.live{border-left-color:var(--good);color:var(--ink2)}
.warnbox{border-left:3px solid var(--warn);padding-left:11px;color:var(--ink2);font-size:13.5px}
pre{font-family:var(--m);font-size:11.5px;line-height:1.55;background:var(--sunk);
    border:1px solid var(--rule);border-radius:4px;padding:10px;margin:0;
    max-height:230px;overflow:auto;white-space:pre-wrap;word-break:break-word}
textarea{width:100%;font-family:var(--m);font-size:11.5px;min-height:70px}
table.srv{width:100%;border-collapse:collapse;font-size:12.5px}
table.srv td{padding:3px 6px;border-top:1px solid var(--rule);font-family:var(--m)}
table.srv td:first-child{color:var(--ink3)}
</style></head><body><div class="wrap">
<h1><small>fmo · lobby NPC editor</small><span id="title">connecting…</span></h1>

<div class="card" id="mapcard">
  <div class="hd"><h2>The floor plan</h2><span class="sub" id="mapsub"> - </span></div>
  <div class="warnbox" id="mapnone" hidden></div>
  <div class="maprow" id="maprow">
    <div class="mapcol">
      <div class="toolbar">
        <div class="bandtabs" id="bandtabs"></div>
        <div class="chips">
          <label><input type="checkbox" id="shFurn" checked> props</label>
          <label><input type="checkbox" id="shVol"> all volumes</label>
          <label><input type="checkbox" id="shHigh"> tall / overhead</label>
          <label><input type="checkbox" id="shNpcs" checked> NPCs</label>
          <label><input type="checkbox" id="shLive" checked> players</label>
          <label><input type="checkbox" id="shServed" checked> served-now ghosts</label>
          <label><input type="checkbox" id="shWalk" checked> walked ground</label>
          <label><input type="checkbox" id="shMarks" checked> SE's marks</label>
          <label><input type="checkbox" id="shLabels"> prop labels</label>
          <button id="fit" style="padding:2px 9px;font-size:12px">fit</button>
          <label title="a terminal placed or dragged within 1.5 m of a map prop lands exactly on it -- the machine is already in the map; the entity makes it selectable"><input type="checkbox" id="snapProp" checked> snap terminals to props</label>
          <label title="mirror left-right, if the game shows it the other way round"><input type="checkbox" id="vFlip"> mirror</label>
          <label title="turn the plan 180°, if you enter from the other side"><input type="checkbox" id="vRot"> rotate 180°</label>
        </div>
      </div>
      <div class="mapwrap"><canvas id="mapcv" width="1024" height="1024"></canvas></div>
      <div class="editmsg" id="editmsg" hidden></div>
      <div class="mapfoot">
        <div class="readout" id="mapread" style="flex:1;min-width:220px"> - </div>
        <span class="key"><i style="background:var(--floor2)"></i>floor</span>
        <span class="key"><i style="background:var(--furn)"></i>furniture</span>
        <span class="key"><i class="r" style="background:#ffffff"></i>NPC</span>
        <span class="key"><i class="r" style="background:#ff9500"></i>selected</span>
        <span class="key"><i class="r" style="background:#ff3b30"></i>player</span>
        <span class="key"><i style="background:linear-gradient(90deg,#3fbf7f,#4c8dff)"></i>walked (low→high)</span>
        <span class="key"><i style="background:#c9a3ff;border-radius:0;transform:rotate(45deg)"></i>SE mark</span>
      </div>
      <p class="why">Scroll to zoom, shift-drag (or middle-drag) to pan. Click empty
      floor to place someone there; click an NPC to select it; drag it to move it,
      drag the orange handle to turn it. +Z is up unless you mirror or rotate the
      view. <b>The coloured cells are the real floor plan</b>: every half-metre a
      real player has stood on (the server's own position log), coloured by height
      so the upper levels read as a different colour; they grow while anyone
      plays and count as floor for placing. Grey boxes are the small props the map
      file carries (consoles, desks, pillars); "all volumes" shows every box the
      file has, most of which are not obstacles at all. Purple diamonds are where
      SE's script puts its cast; hollow ones the script never fills itself.</p>
    </div>
    <div class="side">
      <div class="livebar" id="livebar"></div>
      <div id="listbox">
        <h3><span>Placed in this band</span><span id="npccount"></span></h3>
        <div class="npclist" id="npclist"></div>
      </div>
      <div class="detail" id="npcdetail" hidden></div>
      <div class="placer" id="placer" hidden>
        <div class="at" id="placeat"> - </div>
        <label class="why">role (entity key - what the counter IS)</label>
        <select id="placerole"></select>
        <input id="placekey" placeholder="or any key, e.g. 0x82080940">
        <label class="why">body</label>
        <select id="placebody">
          <option value="person">a person (catalogue body below)</option>
          <option value="terminal">a terminal - the machine its key names (UnitType 30)</option>
        </select>
        <label class="why">person (catalogue typecode - the body)</label>
        <select id="placecat"></select>
        <input id="placelabel" placeholder="label shown in Select-target, e.g. Map.Selector">
        <div class="row2">
          <select id="placeface">
            <option value="">no facing</option>
            <option value="0">faces N (+Z)</option><option value="90">faces E (+X)</option>
            <option value="180">faces S (−Z)</option><option value="270">faces W (−X)</option>
          </select>
          <button id="placego" class="go">place</button>
        </div>
        <button id="placecancel" class="warn">cancel</button>
      </div>
      <div id="notinlayout" class="warnbox" hidden></div>
    </div>
  </div>
</div>

<div class="card">
  <div class="hd"><h2>What the server serves for this band right now</h2><span class="sub" id="servedsub"> - </span></div>
  <div id="servedbox"></div>
  <div class="toolbar" style="margin-top:9px">
    <button id="importgo" class="go">Import the served roster into the editor</button>
    <button id="dropgo" class="warn" data-confirm="Drop this band from the layout file? The server goes back to the env roster for it.">Back to the env roster</button>
  </div>
  <p class="why" style="margin-top:8px">The layout file wins for any band it holds; a band it does not
  hold is served from <span style="font-family:var(--m)">FMO_UDP_POP_NPC*</span> exactly as before.
  U.S.N. lobbies are derived from this O.C.U. layout by SE's own pairing and are not edited separately.</p>
</div>

<div class="card">
  <div class="hd"><h2>Your last edits</h2><span class="sub">what the server answered, newest first</span></div>
  <div id="editsbox" class="why"> - </div>
</div>

<div class="card">
  <div class="hd"><h2>The same layout as an env line</h2><span class="sub">for .env, if you want it to survive without the file</span></div>
  <textarea id="spec" readonly></textarea>
</div>

<div class="card">
  <div class="hd"><h2>Server log</h2><span class="sub" id="layoutpath"></span></div>
  <pre id="log">…</pre>
</div>
</div>
<script>
const T = new URLSearchParams(location.search).get('t') || '';
const q = s => document.querySelector(s);
const qs = p => T ? p + (p.includes('?') ? '&' : '?') + 't=' + encodeURIComponent(T) : p;
function setHTML(el, html){ if(el.dataset.h !== html){ el.dataset.h = html; el.innerHTML = html; } }
function note(s){ const l = q('#log'); l.textContent += '\n' + s; l.scrollTop = l.scrollHeight; }
const SELCOL = '#ff9500';
const COMPASS = ['north','north-east','east','south-east','south','south-west','west','north-west'];
function compass(d){ return d == null ? 'no facing' : COMPASS[Math.round(((d % 360) + 360) % 360 / 45) % 8]; }

let ST = null;          // last state
let PLAN = null;        // {mapno, boxes, extent, walked_n}
let MARKS = [];         // SE's script marks for the band on screen
let WALK = {cell: 0.5, cells: []};   // walked ground cells for the map
let BAND = null;        // band on screen
try{ BAND = localStorage.getItem('fmo-band') || null; }catch(e){}
let VIEW = {cx: 0, cz: 0, s: 20};   // world centre and px per metre (on the 1024 canvas)
let FITTED = null;      // which mapno VIEW was fitted for
let SEL = null;         // selected NPC idx
let PRESS = null;       // a press in progress
let PICK = null;        // an empty-floor click waiting for "place"
const W = 1024, H = 1024;

// ---- projection: +X right, +Z up ------------------------------------------
// view flips: mirror = negate screen x; rotate 180 = negate both. The world
// never changes, only where it is drawn -- a placement stays a world point.
let FLIP = false, ROT = false;
try{ FLIP = localStorage.getItem('fmo-flip') === '1'; ROT = localStorage.getItem('fmo-rot') === '1'; }catch(e){}
function sxz(x, z){ const fx = (FLIP !== ROT) ? -1 : 1, fz = ROT ? -1 : 1; return [fx * (x - VIEW.cx), fz * (z - VIEW.cz)]; }
function w2p(x, z){ const [a, b] = sxz(x, z); return [a * VIEW.s + W / 2, H / 2 - b * VIEW.s]; }
function p2w(px, py){
  const a = (px - W / 2) / VIEW.s, b = (H / 2 - py) / VIEW.s;
  const fx = (FLIP !== ROT) ? -1 : 1, fz = ROT ? -1 : 1;
  return [fx * a + VIEW.cx, fz * b + VIEW.cz];
}
function evPix(e){
  const r = q('#mapcv').getBoundingClientRect();
  return [(e.clientX - r.left) / r.width * W, (e.clientY - r.top) / r.height * H];
}
function fit(){
  if(!PLAN) return;
  let e = PLAN.extent;
  // the walked cells are the true footprint once there are enough of them
  if(WALK.cells.length >= 40){
    const xs = WALK.cells.map(w => w[0]), zs = WALK.cells.map(w => w[1]);
    e = [Math.min(...xs) - 4, Math.min(...zs) - 4, Math.max(...xs) + 4.5, Math.max(...zs) + 4.5];
  }
  const pts = (ST && ST.npcs || []).map(n => [n.x, n.z]);
  if(!e && pts.length) e = [Math.min(...pts.map(p=>p[0]))-5, Math.min(...pts.map(p=>p[1]))-5,
                            Math.max(...pts.map(p=>p[0]))+5, Math.max(...pts.map(p=>p[1]))+5];
  if(!e) e = [-30, -30, 30, 30];
  VIEW.cx = (e[0] + e[2]) / 2; VIEW.cz = (e[1] + e[3]) / 2;
  VIEW.s = Math.min(W / (e[2] - e[0]), H / (e[3] - e[1]));
  FITTED = PLAN.mapno;
}

// ---- floor classification, same rule as fmolayout.ground_at --------------
const ABOVE = 1.0, BELOW = 1.5;
function cls(b, fy){
  const top = b[4];
  if(top < fy - BELOW) return 'under';
  if(top <= fy + ABOVE) return 'floor';
  if(top <= fy + 3.0) return 'furn';
  return 'high';
}
function groundAt(x, z){
  if(!PLAN || ST.floor_y == null) return null;
  let best = null;
  for(const b of PLAN.boxes){
    if(b[0] <= x && x <= b[3] && b[2] <= z && z <= b[5] && cls(b, ST.floor_y) === 'floor')
      if(!best || b[4] > best[4]) best = b;
  }
  return best;
}
// the walked cell under (x, z), or the nearest within 0.75 m -- same rule as the server
function walkedAt(x, z){
  const c = WALK.cell; let best = null, bd = 0.75;
  for(const w of WALK.cells){
    const d = Math.hypot(w[0] + c / 2 - x, w[1] + c / 2 - z);
    if(d < bd){ bd = d; best = w; }
  }
  return best;
}
// floor height under a point for the readouts: box first, then walked ground
function floorAt(x, z){
  // same order as the server: walked under -> a box top players stand at ->
  // the walked floor nearby -> nothing (the server then guesses)
  const w = walkedAt(x, z); if(w) return {y: w[3], how: 'walked'};
  const lv = (ST && ST.levels) || [];
  const g = groundAt(x, z);
  if(g && (!lv.length || lv.some(l => Math.abs(g[4] - l) <= 0.15))) return {y: g[4], how: 'box'};
  let best = null, bd = 8, c = WALK.cell;
  for(const k of WALK.cells){ const d = Math.hypot(k[0] + c / 2 - x, k[1] + c / 2 - z); if(d < bd){ bd = d; best = k; } }
  if(best) return {y: best[3], how: 'walked'};
  return null;
}
// what a box IS, by its shape -- a label, not a decode
function propKind(b, fy){
  const w = b[3] - b[0], d = b[5] - b[2], h = b[4] - b[1], top = b[4] - fy;
  const foot = Math.max(w, d), thin = Math.min(w, d);
  // a slab at floor level is the floor, not a prop; a slab RAISED above it
  // (the 12x12 console platforms top out 0.4 m over the 3.1 tiles) is a platform
  if(h <= 0.6 && top <= 0.25) return '';
  if(h <= 0.6 && top > 0.25 && foot >= 4 && foot <= 20) return 'platform';
  if(thin <= 0.6 && h >= 2.0 && foot >= 2.0) return 'wall';
  if(foot <= 1.3 && h >= 2.0) return 'pillar';
  if(Math.abs(w - 2) < 0.3 && Math.abs(d - 2) < 0.3 && h >= 0.6 && h <= 1.2) return 'console';
  if(foot >= 4 && thin <= 1.8 && h >= 0.7 && h <= 1.5) return 'counter';
  if(h >= 0.6 && h <= 1.4 && foot >= 0.8 && foot <= 4.5) return 'desk';
  if(h < 0.6 && foot <= 4.5) return 'step';
  return '';
}
function propAt(x, z){
  if(!PLAN || ST.floor_y == null) return null;
  let best = null;
  for(const b of PLAN.boxes){
    if(b[0] <= x && x <= b[3] && b[2] <= z && z <= b[5]){
      const c = cls(b, ST.floor_y);
      if((c === 'furn' || c === 'high') && (!best || b[4] < best[4])) best = b;
    }
  }
  return best;
}
function markAt(px, py){
  for(const m of MARKS){ const [a, b] = w2p(m.x, m.z); if(Math.hypot(a - px, b - py) <= 8) return m; }
  return null;
}

// ---- drawing ---------------------------------------------------------------
function drawMap(){
  const cv = q('#mapcv'), g = cv.getContext('2d');
  g.clearRect(0, 0, W, H);
  if(!ST) return;
  const fy = ST.floor_y == null ? 0 : ST.floor_y;
  const css = getComputedStyle(document.documentElement);
  const C = k => css.getPropertyValue(k).trim();
  if(PLAN){
    const showF = q('#shFurn').checked, showH = q('#shHigh').checked, showV = q('#shVol').checked;
    // KEY: MOST BOXES ARE NOT OBSTACLES. Live 2026-09-11: two thirds of the
    // player's walked cells on map 102 lay INSIDE boxes this page drew as
    // solid furniture, and the "floor" slabs were a stack of overlapping
    // volumes. So by default only SMALL boxes are drawn as props (a console,
    // a desk, a pillar); "all volumes" brings the rest back, faint.
    const isProp = b => (b[3] - b[0]) <= 6 && (b[5] - b[2]) <= 6 && (b[4] - b[1]) >= 0.3 && (b[4] - b[1]) <= 2.6;
    const floors = [], furn = [], high = [];
    for(const b of PLAN.boxes){
      const c = cls(b, fy);
      if(!showV && !isProp(b)){ continue; }
      if(c === 'floor') floors.push(b); else if(c === 'furn') furn.push(b); else if(c === 'high') high.push(b);
    }
    floors.sort((a, b) => a[4] - b[4]);
    const rect = b => { const [x0, y0] = w2p(b[0], b[5]), [x1, y1] = w2p(b[3], b[2]);
                        return [Math.min(x0, x1), Math.min(y0, y1), Math.abs(x1 - x0), Math.abs(y1 - y0)]; };
    g.globalAlpha = showV ? 0.5 : 1;
    for(const b of floors){
      const [x0, y0, w, h] = rect(b);
      // a raised platform (top above the floor) reads a shade lighter
      g.fillStyle = b[4] > fy + 0.25 ? C('--floor2') : C('--floor');
      g.fillRect(x0, y0, w, h);
      g.strokeStyle = 'rgba(0,0,0,.25)'; g.lineWidth = 1; g.strokeRect(x0, y0, w, h);
    }
    if(showF) for(const b of furn){
      const [x0, y0, w, h] = rect(b);
      g.fillStyle = C('--furn'); g.fillRect(x0, y0, w, h);
      g.strokeStyle = 'rgba(0,0,0,.45)'; g.lineWidth = 1; g.strokeRect(x0, y0, w, h);
    }
    g.globalAlpha = 1;
    if(showH) for(const b of high){
      const [x0, y0, w, h] = rect(b);
      g.strokeStyle = C('--high'); g.lineWidth = 1; g.setLineDash([3, 3]);
      g.strokeRect(x0, y0, w, h); g.setLineDash([]);
    }
  }
  // walked ground: every cell a real player stood in, coloured by HEIGHT so a
  // hall with several levels reads as several levels (102: 0..13.5 m)
  if(q('#shWalk').checked && WALK.cells.length){
    const c = WALK.cell;
    const ys = WALK.cells.map(w => w[3]), ylo = Math.min(...ys), yhi = Math.max(...ys);
    for(const w of WALK.cells){
      const [ax, ay] = w2p(w[0], w[1]), [bx, by] = w2p(w[0] + c, w[1] + c);
      const t = yhi > ylo ? (w[3] - ylo) / (yhi - ylo) : 0;           // 0 low .. 1 high
      const hue = 150 - 130 * t;                                        // green -> blue
      g.fillStyle = `hsla(${hue.toFixed(0)},80%,${(55 - 15 * t).toFixed(0)}%,0.85)`;
      g.fillRect(Math.min(ax, bx), Math.min(ay, by), Math.max(1, Math.abs(bx - ax)), Math.max(1, Math.abs(by - ay)));
    }
  }
  // prop labels, when asked for
  if(PLAN && q('#shLabels').checked){
    for(const b of PLAN.boxes){
      const c = cls(b, fy);
      if(c !== 'furn' && c !== 'floor') continue;
      const k = propKind(b, fy);
      if(!k || k === 'step' || (c === 'floor' && k !== 'platform')) continue;
      const [x0, y0] = w2p(b[0], b[5]), [x1, y1] = w2p(b[3], b[2]);
      if(Math.abs(x1 - x0) < 24) continue;
      tag(g, k, Math.min(x0, x1) + 2, Math.min(y0, y1) + 9, 'rgba(220,230,245,.9)', 11);
    }
  }
  // SE's script marks: diamonds; hollow = placed but never created = a server slot
  if(q('#shMarks').checked) for(const m of MARKS){
    const [px, py] = w2p(m.x, m.z), r = 7;
    g.beginPath(); g.moveTo(px, py - r); g.lineTo(px + r, py); g.lineTo(px, py + r); g.lineTo(px - r, py); g.closePath();
    g.lineWidth = 2; g.strokeStyle = '#c9a3ff';
    if(m.created){ g.fillStyle = 'rgba(201,163,255,.55)'; g.fill(); }
    g.stroke();
    if(m.face != null){
      const [ux, uy] = hvec(m.face);
      g.beginPath(); g.moveTo(px, py); g.lineTo(px + ux * 14, py + uy * 14); g.stroke();
    }
  }
  // 1 m grid ticks at the origin, so the scale is readable
  const [ox, oy] = w2p(0, 0);
  g.strokeStyle = 'rgba(128,160,200,.35)'; g.lineWidth = 1;
  g.beginPath(); g.moveTo(ox - 12, oy); g.lineTo(ox + 12, oy); g.moveTo(ox, oy - 12); g.lineTo(ox, oy + 12); g.stroke();
  tag(g, '0,0', ox + 6, oy + 12, 'rgba(128,160,200,.8)', 13);
  // served-now ghosts (what the env is serving, if the layout differs)
  if(q('#shServed').checked) for(const n of (ST.served.rows || [])){
    const [px, py] = w2p(n.x, n.z);
    g.beginPath(); g.arc(px, py, 8, 0, 6.2832); g.strokeStyle = 'rgba(180,200,230,.5)';
    g.setLineDash([3, 3]); g.lineWidth = 2; g.stroke(); g.setLineDash([]);
  }
  // NPCs
  if(q('#shNpcs').checked) (ST.npcs || []).forEach(n => {
    const dragging = PRESS && PRESS.moved && PRESS.kind === 'npc' && PRESS.idx === n.idx;
    const [px, py] = dragging ? [PRESS.px, PRESS.py] : w2p(n.x, n.z);
    const isSel = n.idx === SEL;
    const face = isSel && PRESS && PRESS.kind === 'rot' && PRESS.moved ? PRESS.face : n.face;
    const col = isSel ? SELCOL : '#fff';
    g.lineWidth = isSel ? 3.5 : 2; g.strokeStyle = col;
    if(n.terminal){ const r = isSel ? 10 : 7; g.strokeRect(px - r, py - r, 2 * r, 2 * r); }
    else { g.beginPath(); g.arc(px, py, isSel ? 11 : 8, 0, 6.2832); g.stroke(); }
    if(face != null){
      const [ux, uy] = hvec(face), L = isSel ? ROT_R : 14;
      g.beginPath(); g.moveTo(px, py); g.lineTo(px + ux * L, py + uy * L);
      g.lineWidth = isSel ? 3 : 2; g.stroke();
      if(isSel){
        g.beginPath(); g.arc(px + ux * L, py + uy * L, 7, 0, 6.2832);
        g.fillStyle = SELCOL; g.fill(); g.lineWidth = 2; g.strokeStyle = '#000'; g.stroke();
      }
    } else if(isSel){
      // no facing yet: the handle sits at heading 0 so it can be dragged into one
      const [ux, uy] = hvec(0);
      g.beginPath(); g.arc(px + ux * ROT_R, py + uy * ROT_R, 7, 0, 6.2832);
      g.fillStyle = SELCOL; g.fill(); g.lineWidth = 2; g.strokeStyle = '#000'; g.stroke();
    }
    tag(g, n.name, px + 13, py + 16, isSel ? SELCOL : 'rgba(255,255,255,.85)', isSel ? 19 : 15);
  });
  // players in world
  if(q('#shLive').checked) for(const p of (ST.live || [])){
    if(!p.here) continue;
    const [px, py] = w2p(p.x, p.z);
    g.beginPath(); g.arc(px, py, 12, 0, 6.2832); g.strokeStyle = '#ff3b30'; g.lineWidth = 4; g.stroke();
    g.beginPath(); g.arc(px, py, 4, 0, 6.2832); g.fillStyle = '#ff3b30'; g.fill();
    tag(g, p.host, px + 14, py - 14, '#ff3b30', 14);
  }
  // the pick
  if(PICK){
    const [px, py] = w2p(PICK[0], PICK[1]);
    g.beginPath(); g.moveTo(px - 10, py); g.lineTo(px + 10, py); g.moveTo(px, py - 10); g.lineTo(px, py + 10);
    g.strokeStyle = SELCOL; g.lineWidth = 3; g.stroke();
  }
}
const ROT_R = 28;
// a world heading (deg, 0 = +Z, 90 = +X) as a unit screen vector under the view flips
function hvec(deg){
  const a = deg * Math.PI / 180;
  const fx = (FLIP !== ROT) ? -1 : 1, fz = ROT ? -1 : 1;
  return [fx * Math.sin(a), -fz * Math.cos(a)];
}
function tag(g, text, X, Y, col, size){
  g.font = '600 ' + (size || 16) + 'px ui-monospace,Menlo,Consolas,monospace';
  g.textAlign = 'left'; g.textBaseline = 'middle';
  const w = g.measureText(text).width;
  g.fillStyle = 'rgba(8,11,18,.8)'; g.fillRect(X, Y - size * .6, w + 10, size * 1.25);
  g.fillStyle = col; g.fillText(text, X + 5, Y + 1);
}

// ---- side column ------------------------------------------------------------
function selNpc(){ return (ST && ST.npcs || []).find(n => n.idx === SEL) || null; }
function drawTabs(){
  const tab = b => `<button data-band="${b.id}" class="${b.id === ST.band ? 'on' : ''}" title="${b.in_layout ? 'in the layout file' : (b.place ? 'nobody is popped here until you place someone' : 'served from the env')}">`
    + `${b.title}${b.mapno ? ' · map ' + b.mapno : ''} · ${b.n}${b.in_layout ? ' ✎' : ''}</button>`;
  const bs = ST.bands || [];
  setHTML(q('#bandtabs'), bs.filter(b => !b.place).map(tab).join('')
    + `<span class="why" style="align-self:center;padding:0 4px">rooms:</span>`
    + bs.filter(b => b.place).map(tab).join(''));
}
function drawList(){
  const ns = ST.npcs || [];
  q('#npccount').textContent = ns.length || '';
  setHTML(q('#npclist'), ns.length ? ns.map(n =>
    `<button data-idx="${n.idx}" class="${n.idx === SEL ? 'on' : ''}">`
    + `<span>${n.name}<br><span class="k">${n.keyhex} · ${n.person}</span></span>`
    + `<span class="k">${n.x.toFixed(1)}, ${n.z.toFixed(1)}</span></button>`).join('')
    : `<p class="why">${ST.in_layout ? 'Nothing placed in this band yet - click the floor.' : 'This band is served from the env. Import it below, or click the floor to start a layout.'}</p>`);
}
function drawDetail(){
  const n = selNpc(), box = q('#npcdetail');
  if(!n){ box.hidden = true; setHTML(box, ''); return; }
  box.hidden = false;
  const face = PRESS && PRESS.kind === 'rot' && PRESS.moved ? PRESS.face : n.face;
  const ed = o => `data-edit='${JSON.stringify(Object.assign({band: ST.band, idx: n.idx}, o))}'`;
  const turn = d => `<button ${ed({op:'turn', face: (((n.face || 0) + d) % 360 + 360) % 360})}>${d > 0 ? '⟳' : '⟲'} ${Math.abs(d)}°</button>`;
  const faceb = (y, l) => `<button ${ed({op:'turn', face: y})}>${l}</button>`;
  const cats = (ST.people || []).map(p => `<option value="${p.cat}" ${p.cat === n.cat ? 'selected' : ''}>${p.cat} · ${p.name}</option>`).join('');
  setHTML(box,
    `<div class="t">${n.name}</div>`
    + `<dl><dt>entity key</dt><dd>${n.keyhex} · row ${n.idx}</dd>`
    + `<dt>body</dt><dd>${n.person}${n.cat != null ? ' · #' + n.cat : ''}</dd>`
    + `<dt>at</dt><dd>${n.x.toFixed(2)}, ${n.z.toFixed(2)} · floor ${n.y.toFixed(2)}</dd>`
    + `<dt>facing</dt><dd>${face == null ? 'none' : face.toFixed(0) + '° · ' + compass(face)}</dd></dl>`
    + `<div class="acts">${turn(-45)}${turn(-15)}${turn(15)}${turn(45)}</div>`
    + `<div class="acts">${faceb(0,'N')}${faceb(90,'E')}${faceb(180,'S')}${faceb(270,'W')}`
    + `<button ${ed({op:'turn', face: null})}>none</button></div>`
    + `<div class="row2"><select id="recast"><option value="none">player's own body</option>${cats}</select>`
    + `<button id="recastgo">recast</button></div>`
    + `<div class="row2"><input id="relabel" value="${(n.label || '').replace(/"/g, '&quot;')}" placeholder="Name.Label">`
    + `<button id="relabelgo">relabel</button></div>`
    + `<dl><dt>client kind</dt><dd>${n.ckind == null ? 'server default (1, no peer)' : n.ckind + (n.ckind === 0 ? ' (a peer -- the name-tag test)' : '')}</dd></dl>`
    + `<div class="acts">${n.ckind === 0
        ? `<button ${ed({op:'ckind', ckind:'default'})}>client kind: back to default</button>`
        : `<button ${ed({op:'ckind', ckind:0})} title="pop it with a peer object (client_kind 0), the way every NPC went out before 2026-09-05 -- does a name tag appear?">name-tag test: client kind 0</button>`}</div>`
    + `<div class="acts">${n.terminal
        ? `<button ${ed({op:'body', body:'person'})}>make it a person</button>`
        : `<button ${ed({op:'body', body:'terminal'})} title="pop it with no body -- SE's lobby shot shows MAP.SELECTOR as a floating label at the console">make it a terminal (the machine)</button>`}`
    + `<button class="warn" ${ed({op:'remove'})} data-confirm="Remove ${n.name}?">Remove</button></div>`
    + `<p class="why">Drag the NPC to move it; drag the orange handle to turn it. A move shows in game on the next re-pop; a removal only after re-entering the lobby.</p>`);
}
function drawServed(){
  const s = ST.served;
  q('#servedsub').textContent = s.source;
  setHTML(q('#servedbox'), s.rows.length
    ? `<table class="srv">${s.rows.map(r => `<tr><td>${r.keyhex}</td><td>${r.name}</td><td>${r.person}</td>`
        + `<td>${r.x.toFixed(2)}, ${r.y.toFixed(2)}, ${r.z.toFixed(2)}</td><td>${r.face == null ? '' : r.face + '°'}</td></tr>`).join('')}</table>`
    : '<p class="why">nothing is served for this band</p>');
  q('#importgo').disabled = !s.rows.length;
  q('#dropgo').disabled = !ST.in_layout;
}
function drawLive(){
  const b = q('#livebar');
  const here = (ST.live || []).filter(p => p.here), any = (ST.live || []);
  b.className = 'livebar' + (here.length ? ' live' : '');
  const where = p => (p.band ? `the ${(ST.bands.find(x => x.id === p.band) || {title: p.band}).title} tab (zone ${p.zone})` : `map ${p.mapno}`);
  b.textContent = here.length
    ? `${here.length} player(s) in THIS band - edits here reach them on the next NPC re-pop.`
    : any.length ? `${any.length} player(s) in world, but in ${[...new Set(any.map(where))].join(' / ')} - edit THAT tab to reach them. Edits here are saved for the next entry.`
                 : 'Nobody in world - edits are saved and served on the next entry.';
}

function render(s){
  ST = s;
  q('#title').textContent = `${s.title} · map ${s.mapno ?? '?'}`;
  q('#layoutpath').textContent = s.layout_path || '';
  const none = q('#mapnone');
  if(s.mapno == null){
    none.hidden = false; none.textContent = `No map is served for the ${s.band} band (FMO_ZONE_MAPNO). The editor needs one to draw.`;
  } else if(!s.has_plan){
    none.hidden = false; none.textContent = `No floor plan ships for map ${s.mapno} (fmodata/floorplans). Run tools/fmo_floorplans.py.`;
  } else none.hidden = true;
  q('#mapsub').textContent = `${s.desc} · floor y ${s.floor_y ?? '?'}` + (PLAN ? ` · ${PLAN.boxes.length} boxes` : '');
  if(SEL !== null && !(s.npcs || []).some(n => n.idx === SEL)) SEL = null;
  const nl = q('#notinlayout');
  nl.hidden = s.in_layout;
  nl.textContent = s.in_layout ? '' : (s.place_kind
    ? `Nobody is popped in a ${s.title} today -- SE's tables carry room and hangar staff keys (room_event, hanger_event, tag_training_main) but no position was ever measured. Place someone and go look: whether they render and talk in a room is the live test.`
    : `The ${s.band} band is served from the env today. The first thing you place starts a layout for it; "Import" below copies what is served now so you can move it.`);
  const miss = (s.required || []).filter(r => !r.placed);
  if(miss.length){ nl.hidden = false; nl.textContent = `SE's hangar gate: the owner's SETUP.CONSOLE and PILOT.LOCKER open only when ALL of ${(s.required || []).map(r => r.keyhex + ' ' + r.label).join(', ')} are placed here. Missing: ${miss.map(r => r.keyhex + ' ' + r.label).join(', ')}. (The server adds one unit per saved wanzer setup in the bays by itself.)`; }
  if(s.in_layout && s.layout_mapno && s.mapno && s.layout_mapno !== s.mapno)
    { nl.hidden = false; nl.textContent = `WARNING: This layout was authored on map ${s.layout_mapno} but the server now serves map ${s.mapno} for this band - heights and props will not match.`; }
  drawTabs(); drawList(); drawDetail(); drawServed(); drawLive();
  q('#spec').value = s.spec || '';
  setHTML(q('#editsbox'), (s.edits || []).length ? '<table class="srv">' + s.edits.map(e =>
    `<tr><td>${e.t}</td><td>${e.band || ''}</td><td>${e.op}</td><td style="color:${e.ok ? 'var(--good)' : 'var(--warn)'}">${e.ok ? 'ok' : 'REFUSED'}</td><td style="font-family:inherit">${e.msg}</td></tr>`).join('') + '</table>' : 'nothing yet');
  const l = q('#log');
  const stuck = l.scrollTop + l.clientHeight >= l.scrollHeight - 20;
  l.textContent = (s.log || []).join('\n');
  if(stuck) l.scrollTop = l.scrollHeight;
  if(PLAN && FITTED !== PLAN.mapno) fit();
  drawMap();
}

async function tick(){
  try{
    const needPlan = !PLAN || PLAN.stale || (ST && ST.mapno !== PLAN.mapno);
    const r = await fetch(qs('/state?band=' + encodeURIComponent(BAND || '') + (needPlan ? '&plan=1' : '')));
    const s = await r.json();
    if(s.error){ note('state error: ' + s.error); return; }
    if(s.plan){ PLAN = s.plan; MARKS = s.marks || []; WALK = s.walked || {cell: 0.5, cells: []}; }
    else if(PLAN && s.mapno !== PLAN.mapno){ PLAN = null; MARKS = []; WALK = {cell: 0.5, cells: []}; }
    // somebody is walking: refetch the overlay bundle next poll (the view is kept)
    else if(PLAN && s.walked_n !== PLAN.walked_n){ PLAN.stale = true; }
    if(!BAND) BAND = s.band;
    if(!PRESS) render(s); else ST = s;
  }catch(e){ note('poll failed: ' + e); }
}
async function edit(op){
  let res;
  try{
    const r = await fetch(qs('/edit'), {method:'POST', body: JSON.stringify(op)});
    res = await r.json();
  }catch(e){ res = {ok:false, msg:'could not reach the server: ' + e}; }
  const m = q('#editmsg');
  m.hidden = false; m.className = 'editmsg ' + (res.ok ? 'ok' : 'bad');
  m.textContent = res.msg || (res.ok ? 'done' : 'refused');
  setTimeout(tick, 120);
  return res;
}

// ---- pointer ---------------------------------------------------------------
function hit(px, py){
  if(!ST) return null;
  const sn = selNpc();
  if(sn){
    const [nx, ny] = w2p(sn.x, sn.z), [ux, uy] = hvec(sn.face == null ? 0 : sn.face);
    const hx = nx + ux * ROT_R, hy = ny + uy * ROT_R;
    if(Math.hypot(hx - px, hy - py) <= 10) return {kind: 'rot', idx: sn.idx, face: sn.face};
  }
  if(q('#shNpcs').checked){
    const n = (ST.npcs || []).find(n => { const [a, b] = w2p(n.x, n.z); return Math.hypot(a - px, b - py) <= 12; });
    if(n) return {kind: 'npc', idx: n.idx};
  }
  return null;
}
const cv = q('#mapcv');
cv.addEventListener('mousedown', e => {
  if(!ST) return;
  const [px, py] = evPix(e);
  if(e.button === 1 || (e.button === 0 && e.shiftKey)){
    PRESS = {kind: 'pan', px, py, px0: px, py0: py, cx0: VIEW.cx, cz0: VIEW.cz, moved: false};
    e.preventDefault(); return;
  }
  if(e.button !== 0) return;
  PRESS = Object.assign({px, py, px0: px, py0: py, moved: false}, hit(px, py) || {kind: 'none'});
  e.preventDefault();
});
window.addEventListener('mousemove', e => {
  if(!PRESS) return;
  const [px, py] = evPix(e);
  PRESS.px = px; PRESS.py = py;
  if(Math.hypot(px - PRESS.px0, py - PRESS.py0) > 3) PRESS.moved = true;
  if(!PRESS.moved) return;
  if(PRESS.kind === 'pan'){
    VIEW.cx = PRESS.cx0 - (px - PRESS.px0) / VIEW.s; VIEW.cz = PRESS.cz0 + (py - PRESS.py0) / VIEW.s;
    drawMap(); return;
  }
  if(PRESS.kind === 'rot'){
    const n = selNpc(); if(!n) return;
    const [nx, ny] = w2p(n.x, n.z);
    // the pointer's direction from the NPC, back in WORLD terms (undo the flips)
    const [wx, wz] = p2w(px, py);
    const deg = Math.atan2(wx - n.x, wz - n.z) * 180 / Math.PI;
    PRESS.face = Math.round(((deg % 360) + 360) % 360 / 5) * 5 % 360;
    q('#mapread').textContent = `turning ${n.name} to ${PRESS.face}° · ${compass(PRESS.face)}`;
    drawMap(); drawDetail(); return;
  }
  if(PRESS.kind === 'npc'){
    const [x, z] = p2w(px, py), f = floorAt(x, z);
    q('#mapread').textContent = `moving to ${x.toFixed(2)}, ${z.toFixed(2)}` + (f ? ` · floor ${f.y.toFixed(2)}${f.how === 'walked' ? ' (walked)' : ''}` : ' · NO FLOOR HERE');
    drawMap();
  }
});
window.addEventListener('mouseup', e => {
  if(!PRESS) return;
  const p = PRESS; PRESS = null;
  if(!ST) return;
  if(p.kind === 'pan'){ return; }
  if(p.moved){
    const [x, z] = p2w(p.px, p.py);
    if(p.kind === 'npc') edit({op:'move', band: ST.band, idx: p.idx, x: +x.toFixed(2), z: +z.toFixed(2), snap: q('#snapProp').checked});
    else if(p.kind === 'rot') edit({op:'turn', band: ST.band, idx: p.idx, face: p.face});
    else if(ST) render(ST);
    return;
  }
  if(e.target !== cv) return;
  if(p.kind === 'npc' || p.kind === 'rot'){
    SEL = p.idx; PICK = null; q('#placer').hidden = true; render(ST); return;
  }
  // empty floor: offer to place
  const [x, z] = p2w(p.px, p.py), f = floorAt(x, z);
  SEL = null;
  PICK = [x, z];
  q('#placeat').textContent = `place at ${x.toFixed(2)}, ${z.toFixed(2)}` + (f ? ` · floor ${f.y.toFixed(2)}${f.how === 'walked' ? ' (walked ground)' : ''}` : ' · WARNING: no floor here - the server will refuse');
  const placed = new Set((ST.npcs || []).map(n => n.key));
  setHTML(q('#placerole'), (ST.roles || []).map(r =>
    `<option value="${r.keyhex}" ${placed.has(r.key) ? 'disabled' : ''}>${r.keyhex} · ${r.label}${r.terminal ? ' - ' + r.terminal + ' (a terminal in the original)' : ''}${placed.has(r.key) ? ' (placed)' : ''}</option>`).join('')
    + '<option value=""> - type a key below - </option>');
  setHTML(q('#placecat'), '<option value="none">player\'s own body</option>' + (ST.people || []).map(p =>
    `<option value="${p.cat}">${p.cat} · ${p.name}</option>`).join(''));
  q('#placer').hidden = false;
  render(ST);
});
cv.addEventListener('wheel', e => {
  if(!PLAN) return;
  e.preventDefault();
  const [px, py] = evPix(e), [wx, wz] = p2w(px, py);
  const f = Math.exp(-e.deltaY * 0.0015);
  VIEW.s = Math.max(2, Math.min(200, VIEW.s * f));
  // keep the point under the cursor fixed
  const [nx, ny] = w2p(wx, wz);
  VIEW.cx += (nx - px) / VIEW.s; VIEW.cz -= (ny - py) / VIEW.s;
  drawMap();
}, {passive: false});
cv.addEventListener('mousemove', e => {
  if(!ST || PRESS) return;
  const [px, py] = evPix(e), [x, z] = p2w(px, py);
  const t = hit(px, py);
  const f = floorAt(x, z);
  let what = '';
  if(t && t.kind === 'npc'){ const n = ST.npcs.find(n => n.idx === t.idx); what = `  ${n.name} · ${n.keyhex} · facing ${compass(n.face)}`; }
  else if(t && t.kind === 'rot') what = '  drag to turn';
  else {
    const m = q('#shMarks').checked ? markAt(px, py) : null;
    if(m) what = `  SE mark id 0x${m.sid.toString(16)}${m.created ? '' : ' - never created by the script: a server slot'}${m.face != null ? ' · faces ' + compass(m.face) : ''}`;
    else { const p = propAt(x, z); if(p){ const k = propKind(p, ST.floor_y); what = `  ${k || 'prop'} ${(p[3]-p[0]).toFixed(1)}×${(p[5]-p[2]).toFixed(1)}×${(p[4]-p[1]).toFixed(1)} m, top ${p[4].toFixed(2)}`; } }
  }
  cv.style.cursor = t ? 'grab' : (f ? 'cell' : 'not-allowed');
  q('#mapread').textContent = `${x.toFixed(2)}, ${z.toFixed(2)} · ` + (f ? `floor ${f.y.toFixed(2)}${f.how === 'walked' ? ' (walked)' : ''}` : 'no floor') + what;
});
cv.addEventListener('mouseleave', () => { if(!PRESS) q('#mapread').textContent = ' - '; });
cv.addEventListener('contextmenu', e => e.preventDefault());

// ---- buttons ---------------------------------------------------------------
for(const id of ['shFurn','shVol','shHigh','shNpcs','shLive','shServed','shWalk','shMarks','shLabels']) q('#' + id).addEventListener('change', drawMap);
q('#vFlip').checked = FLIP; q('#vRot').checked = ROT;
q('#vFlip').addEventListener('change', e => { FLIP = e.target.checked; try{ localStorage.setItem('fmo-flip', FLIP ? '1' : '0'); }catch(err){} drawMap(); });
q('#vRot').addEventListener('change', e => { ROT = e.target.checked; try{ localStorage.setItem('fmo-rot', ROT ? '1' : '0'); }catch(err){} drawMap(); });
q('#fit').addEventListener('click', () => { fit(); drawMap(); });
q('#bandtabs').addEventListener('click', e => {
  const b = e.target.closest('[data-band]'); if(!b) return;
  BAND = b.dataset.band; SEL = null; PICK = null; q('#placer').hidden = true; PLAN = null; FITTED = null;
  try{ localStorage.setItem('fmo-band', BAND); }catch(err){}
  tick();
});
q('#npclist').addEventListener('click', e => {
  const b = e.target.closest('[data-idx]'); if(!b) return;
  const i = +b.dataset.idx; SEL = (SEL === i ? null : i); PICK = null; q('#placer').hidden = true;
  if(ST) render(ST);
});
document.addEventListener('click', e => {
  const b = e.target.closest('button[data-edit]'); if(!b || b.disabled) return;
  if(b.dataset.confirm && !confirm(b.dataset.confirm)) return;
  edit(JSON.parse(b.dataset.edit));
});
q('#placerole').addEventListener('change', () => {
  const role = (ST.roles || []).find(r => r.keyhex === q('#placerole').value);
  if(role && role.terminal){
    q('#placebody').value = 'terminal';
    q('#placelabel').value = role.terminal;
  }
});
q('#placego').addEventListener('click', () => {
  if(!PICK || !ST) return;
  const key = q('#placekey').value.trim() || q('#placerole').value;
  if(!key){ note('pick a role or type a key'); return; }
  const role = (ST.roles || []).find(r => r.keyhex === key);
  const label = q('#placelabel').value.trim() || (role && /^[A-Za-z]+\.[A-Za-z]+$/.test(role.label) ? role.label : '');
  edit({op:'place', band: ST.band, key, cat: q('#placecat').value, x: +PICK[0].toFixed(2), z: +PICK[1].toFixed(2),
        face: q('#placeface').value, label, body: q('#placebody').value, snap: q('#snapProp').checked});
  PICK = null; q('#placer').hidden = true; q('#placekey').value = ''; q('#placelabel').value = '';
});
q('#placecancel').addEventListener('click', () => { PICK = null; q('#placer').hidden = true; drawMap(); });
q('#importgo').addEventListener('click', () => ST && edit({op:'import', band: ST.band}));
q('#dropgo').addEventListener('click', () => {
  if(!ST) return;
  if(!confirm(q('#dropgo').dataset.confirm)) return;
  edit({op:'drop_band', band: ST.band});
});
q('#npcdetail').addEventListener('click', e => {
  const n = selNpc(); if(!n) return;
  if(e.target.id === 'recastgo') edit({op:'recast', band: ST.band, idx: n.idx, cat: q('#recast').value});
  if(e.target.id === 'relabelgo') edit({op:'relabel', band: ST.band, idx: n.idx, label: q('#relabel').value.trim()});
});
tick(); setInterval(tick, 1500);
</script></body></html>
"""


# --------------------------------------------------------------------------- #
def make_ctx(served, live=None, face_sign=-1, layout=None):
    return {"layout": layout or fmolayout.Layout(), "served": served,
            "live": live or [], "face_sign": face_sign}


#: prod's HQ roster on 2026-09-11 -- the stand-alone preview's and the
#: browser test's "what is served"; a walked survey, confirmed in a live session
PROD_HQ_SPEC = ("0x82080200@-1.35,3.50,2.74#113=Scramble.Board;"
                "0x82080400@1.50,3.50,2.53#109=Map.Selector;"
                "0x82080100@-2.58,3.11,6.09#115=Mission.Counter;"
                "0x82080500@4.23,3.11,5.85#120=Personnel.Officer;"
                "0x82081010@3.29,3.11,8.59#100=Kwangsu.Son;"
                "0x82081020@10.61,2.99,7.88#102=Henry.Viduka;"
                "0x82080110@11.18,2.99,1.20#103=Edward.Miura;"
                "0x82080600@2.60,3.50,2.53#109=Sortie.Console;"
                "0x82080c00@3.70,3.50,2.53#109=Hangar.Console")


def parse_spec_simple(spec):
    """The env grammar -> (roster, names), for contexts without fmo.py."""
    roster, names = [], {}
    for e in spec.replace(" ", "").split(";"):
        if not e:
            continue
        e, _, cat = e.partition("#")
        cat, _, nm = cat.partition("=")
        body, _, pos = e.partition("@")
        idstr, _, ty = body.partition(":")
        uid = int(idstr, 0)
        p = tuple(float(x) for x in pos.split(",")) if pos else None
        if p is not None and len(p) == 3:
            p = p + (0.0,)
        roster.append((uid, int(ty, 0) if ty else 4, p, int(cat, 0) if cat else None))
        if nm:
            n1, _, n2 = nm.partition(".")
            names[uid] = (n1, n2)
    return roster, names


def demo_walked(path):
    """A small position log: a few steps by Kwangsu Son, and one cell out in
    the void at (150, 150) so the walked-ground fallback can be exercised."""
    lines = ["x [udp 1.1.1.1:1] POSITION MapNo=102 (%.2f, 3.11, %.2f) rot +0.0 flags 0x6"
             % (3.0 + i * 0.3, 8.6 + (i % 2) * 0.2) for i in range(12)]
    # somebody stood on the west console platform (y 3.50), as prod's log has
    lines += ["x [udp 1.1.1.1:1] POSITION MapNo=102 (%.2f, 3.50, %.2f) rot +0.0 flags 0x6"
              % p for p in ((-0.7, 2.3), (-0.6, 2.4), (0.3, 1.9), (0.4, 1.8))]
    lines += ["x [udp 1.1.1.1:1] POSITION MapNo=102 (150.10, 3.00, 150.20) rot +0.0 flags 0x6"] * 3
    lines += ["x [udp 1.1.1.1:1] POSITION MapNo=102 (1.93, 0.00, -1.12) rot +2.5 flags 0x6  <- SPAWN: no"]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return fmolayout.WalkedGround(path)


def demo_ctx(layout_path=None):
    r, n = parse_spec_simple(PROD_HQ_SPEC)
    served = {"hq": {"mapno": 102, "floor_y": 3.11, "roster": r, "names": n,
                     "source": "FMO_UDP_POP_NPC (prod, 2026-09-11)"},
              "occ": {"mapno": 102, "floor_y": 3.11, "roster": r, "names": n,
                      "source": "derived from FMO_UDP_POP_NPC"},
              "fz": {"mapno": 102, "floor_y": 3.11, "roster": r, "names": n,
                     "source": "derived from FMO_UDP_POP_NPC"},
              "col": {"mapno": 161, "floor_y": 16.0, "roster": [], "names": {},
                      "source": "FMO_UDP_POP_NPC_COL default"}}
    ctx = make_ctx(served, layout=fmolayout.Layout(layout_path))
    ctx["walked"] = demo_walked(os.path.join(os.path.dirname(layout_path or DEFAULT_TMP), "walked.log"))
    return ctx


DEFAULT_TMP = os.path.join(os.environ.get("TEMP", "/tmp"), "fmodevtool-demo.json")


def selftest():
    import tempfile
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print("  %s: %s%s" % (name, "OK" if cond else "FAIL", (" " + detail) if detail and not cond else ""))
        ok &= bool(cond)

    tmp = tempfile.mkdtemp(prefix="fmodevtool-")
    ctx = demo_ctx(os.path.join(tmp, "layout.json"))
    st = build_state(ctx, "hq", want_plan=True)
    check("state: hq on map 102, env-served, 9 served rows",
          st["mapno"] == 102 and not st["in_layout"] and st["served"]["n"] == 9, json.dumps(st["served"]["n"]))
    check("state carries the plan once asked", "plan" in st and len(st["plan"]["boxes"]) > 1000)
    check("state without plan is small", "plan" not in build_state(ctx, "hq"))
    ctx["served"]["hangar"] = {"mapno": 141, "floor_y": 0.5, "roster": [], "names": {}, "source": "t"}
    r = apply_edit(ctx, {"op": "place", "band": "hangar", "key": "0x82080c00", "x": -5.72, "z": 9.25,
                         "body": "terminal", "label": "SETUP.CONSOLE", "snap": True})
    t = build_state(ctx, "hangar")["npcs"][0]
    check("a terminal snaps onto the map's console prop (141: -6.10, 8.44)",
          r["ok"] and "SNAPPED" in r["msg"] and (t["x"], t["z"]) == (-6.1, 8.44), r["msg"])
    r = apply_edit(ctx, {"op": "place", "band": "hangar", "key": "0x82080c01", "x": -5.72, "z": 9.25,
                         "body": "terminal", "label": "SETUP.CONSOLE", "snap": False})
    check("...and does not when snap is off", r["ok"] and "SNAPPED" not in r["msg"], r["msg"])
    ctx["layout"].drop_band("hangar")
    check("SE's terminal names ride the roles",
          next(r for r in st["roles"] if r["key"] == 0x82080400)["terminal"] == "MAP.SELECTOR"
          and next(r for r in build_state(ctx, "hangar")["roles"] if r["key"] == 0x82080C00)["terminal"] == "SETUP.CONSOLE")
    check("roles and people for hq", any(r["key"] == 0x82080400 for r in st["roles"])
          and any(p["cat"] == 100 for p in st["people"]) and not any(p["cat"] == 30 for p in st["people"]))
    check("people for fz are D07's", any(p["cat"] == 30 for p in build_state(ctx, "fz")["people"]))

    # place on the floor, the platform, and the void
    r = apply_edit(ctx, {"op": "place", "band": "hq", "key": "0x82080940", "cat": 108,
                         "x": 3.0, "z": 12.0, "face": "90", "label": "Ranking.Board"})
    check("place on the floor", r["ok"] and "floor 3.1" in r["msg"], r["msg"])
    r = apply_edit(ctx, {"op": "place", "band": "hq", "key": "0x82080950", "cat": None,
                         "x": -0.5, "z": 2.0, "face": ""})
    check("place on the platform snaps to 3.50", r["ok"] and "floor 3.50" in r["msg"], r["msg"])
    ctx["served"]["hq"]["floor_y"] = None
    _w = ctx.pop("walked")
    r = apply_edit(ctx, {"op": "place", "band": "hq", "key": "0x82080951", "x": 120, "z": 120})
    check("the void with NO known floor is refused", not r["ok"] and "no floor" in r["msg"], r["msg"])
    ctx["walked"] = _w
    ctx["served"]["hq"]["floor_y"] = 3.11
    r = apply_edit(ctx, {"op": "place", "band": "hq", "key": "0x82080952", "x": 150.2, "z": 150.3})
    check("...but WALKED ground with no box is accepted, with its mean y",
          r["ok"] and "walked ground" in r["msg"] and "floor 3.00" in r["msg"], r["msg"])
    r2 = apply_edit(ctx, {"op": "place", "band": "hq", "key": "0x82080953", "x": 120.5, "z": 120.5})
    check("the void with a known map floor is a GUESS, said so, at the walked floor",
          r2["ok"] and "GUESSED" in r2["msg"] and "floor 3.10" in r2["msg"], r2["msg"])
    apply_edit(ctx, {"op": "remove", "band": "hq", "idx": len(build_state(ctx, "hq")["npcs"]) - 1})
    check("edits are remembered for the page", build_state(ctx, "hq")["edits"][0]["op"] == "remove")
    apply_edit(ctx, {"op": "remove", "band": "hq", "idx": 2})
    st = build_state(ctx, "hq", want_plan=True)
    check("the plan bundle carries SE's marks and walked cells; the poll carries the count",
          len(st["marks"]) > 20 and len(st["walked"]["cells"]) >= 2 and st["walked_n"] == 19
          and "marks" not in build_state(ctx, "hq") and build_state(ctx, "hq")["walked_n"] == 19,
          json.dumps([len(st["marks"]), len(st["walked"]["cells"]), st["walked_n"]]))
    r = apply_edit(ctx, {"op": "place", "band": "hq", "key": "0x82080401", "cat": 108,
                         "x": 1.5, "z": 2.5, "body": "terminal", "label": "Map.Selector"})
    st = build_state(ctx, "hq")
    t = next(n for n in st["npcs"] if n["key"] == 0x82080401)
    check("a TERMINAL pops as UnitType 30 with no catalogue body and an uppercased label",
          r["ok"] and "TERMINAL" in r["msg"] and t["type"] == 30 and t["cat"] is None
          and t["label"] == "MAP.SELECTOR" and t["terminal"], json.dumps(t))
    ro, nm = ctx["layout"].roster("hq", -1)
    check("...and reaches the server roster as (key, 30, pos, None) with the names",
          any(e[0] == 0x82080401 and e[1] == 30 and e[3] is None for e in ro)
          and nm[0x82080401] == ("MAP", "SELECTOR"))
    r = apply_edit(ctx, {"op": "ckind", "band": "hq", "idx": t["idx"], "ckind": 0})
    check("ckind 0 on a row, and the layout collects it",
          r["ok"] and "NAME TAG" in r["msg"] and ctx["layout"].ckinds("hq") == {0x82080401: 0}, r["msg"])
    r = apply_edit(ctx, {"op": "ckind", "band": "hq", "idx": t["idx"], "ckind": "default"})
    check("ckind back to default", r["ok"] and ctx["layout"].ckinds("hq") == {})
    r = apply_edit(ctx, {"op": "body", "band": "hq", "idx": t["idx"], "body": "person"})
    check("body toggle back to a person", r["ok"] and build_state(ctx, "hq")["npcs"][t["idx"]]["type"] == 4)
    apply_edit(ctx, {"op": "remove", "band": "hq", "idx": t["idx"]})
    check("a place band gets no lobby marks",
          build_state(ctx, "room", want_plan=True).get("marks") in ([], None))
    r = apply_edit(ctx, {"op": "place", "band": "hq", "key": "0x82080940", "x": 3.0, "z": 12.0})
    check("a second body on one key is refused", not r["ok"] and "already" in r["msg"], r["msg"])
    st = build_state(ctx, "hq")
    check("layout now has 2 rows and the band is in the layout",
          st["in_layout"] and len(st["npcs"]) == 2 and st["npcs"][0]["name"] == "Ranking Board"
          and st["npcs"][0]["face"] == 90.0, json.dumps(st["npcs"]))
    check("spec line renders", st["spec"].startswith("0x82080940@3,3.1"), st["spec"])
    r = apply_edit(ctx, {"op": "move", "band": "hq", "idx": 0, "x": -1.0, "z": 2.5})
    check("move re-snaps the floor", r["ok"] and "floor 3.50" in r["msg"], r["msg"])
    r = apply_edit(ctx, {"op": "turn", "band": "hq", "idx": 0, "face": 225})
    check("turn", r["ok"] and build_state(ctx, "hq")["npcs"][0]["face"] == 225.0)
    r = apply_edit(ctx, {"op": "turn", "band": "hq", "idx": 0, "face": None})
    check("no facing", r["ok"] and build_state(ctx, "hq")["npcs"][0]["face"] is None)
    r = apply_edit(ctx, {"op": "recast", "band": "hq", "idx": 0, "cat": 102})
    check("recast", r["ok"] and "Henry Viduka" in r["msg"], r["msg"])
    r = apply_edit(ctx, {"op": "relabel", "band": "hq", "idx": 0, "label": "Battle.Ranking"})
    check("relabel", r["ok"] and build_state(ctx, "hq")["npcs"][0]["name"] == "Battle Ranking")
    r = apply_edit(ctx, {"op": "remove", "band": "hq", "idx": 1})
    check("remove says the body stays on screen", r["ok"] and "re-enters" in r["msg"], r["msg"])
    # the roster the world channel would pop from
    ro, nm = ctx["layout"].roster("hq", -1)
    check("layout roster -> server shape", ro == [(0x82080940, 4, (-1.0, 3.5, 2.5, 0.0), 102)]
          and nm[0x82080940] == ("Battle", "Ranking"), str((ro, nm)))
    # import and drop
    r = apply_edit(ctx, {"op": "import", "band": "occ"})
    st = build_state(ctx, "occ")
    check("import copies the served roster", r["ok"] and len(st["npcs"]) == 9
          and st["npcs"][4]["name"] == "Kwangsu Son" and st["npcs"][4]["y"] == 3.11, r["msg"])
    r = apply_edit(ctx, {"op": "drop_band", "band": "occ"})
    check("drop_band", r["ok"] and not build_state(ctx, "occ")["in_layout"])
    r = apply_edit(ctx, {"op": "move", "band": "fz", "idx": 0, "x": 1, "z": 1})
    check("move in a band not in the layout is refused", not r["ok"] and "import" in r["msg"], r["msg"])
    r = apply_edit(ctx, {"op": "place", "band": "nope", "key": 1, "x": 0, "z": 0})
    check("bad band", not r["ok"])
    check("page names its title", "<title>fmo lobby editor</title>" in PAGE)
    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def serve_standalone(port):
    import fedevtool
    import tempfile
    tmp = tempfile.mkdtemp(prefix="fmodevtool-")
    ctx = demo_ctx(os.path.join(tmp, "layout.json"))
    fedevtool.start(port, "127.0.0.1", "", lambda band=None: build_state(ctx, band, want_plan=True),
                    lambda op: apply_edit(ctx, op), page=PAGE, name="fmo-devtool")
    print("http://127.0.0.1:%d/   (layout: %s)" % (port, ctx["layout"].path))
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    if "--serve" in sys.argv:
        serve_standalone(int(sys.argv[sys.argv.index("--serve") + 1]))
    else:
        sys.exit(selftest())
