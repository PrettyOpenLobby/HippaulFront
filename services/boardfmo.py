#!/usr/bin/env python3
"""boardfmo.py -- FRONT MISSION ONLINE's CITY CONTROL as a live board
(polboards, fmo.example.com, 2026-09-12).

What it shows is the war the Personnel Officer's City Control screen draws
(live since 2026-09-12 15:42Z): SE's nineteen economic cities (fmowar.CITIES,
from guide/phase.html), who holds each, its B.G.Cost and its Rank, the phase
score O.C.U. vs U.S.N. (the sum of the ranks each side holds, fmowar's own
War.score), and the phase clock (fmowar.phase_at). The words are SE's own
(the client's systext table, group 30: City Control / City Name / Control /
B.G.Cost / Rank / O.C.U. / U.S.N. / Deadlock). The look is SE's war map: grey
panels over the zone's satellite image (tools/fmo_boardart_bake.py).

It READS three files and writes none of them:
  * /data/fmowar.json ($FMO_WAR_STATE) -- the war. It may be MISSING: fmo.py
    creates it on first use. Then every city is Deadlock and the board says
    there is no war state on file.
  * /data/fmo_sector_wins.json -- the sector-win ledger (fmo.py
    sector_win_record): "zone:tile:nation" -> [unix times], kept two days.
    The "Recent battles" sidebar.
  * /data/fmo-sessions-live.json -- {count, stamp}, rewritten every 10 s:
    TCP CONNECTIONS, not players (one player can be two). Labelled so.

THE ONE TRAP (fmowar's docstring has the rest): War.tick() JUDGES phases and
Deadlocks the loser's fortress, and War.sector(tile) INSERTS a default for an
unknown tile. This module only ever touches War(path, autosave=False).data and
.score() -- both pure reads -- and the pure fmowar.phase_at().

HONESTY. SE never published its numbers: the opening map (control 100 %,
occupied 60 %, frontline Deadlock), the counter cap and the rate step are our
labelled defaults, and the B.G.Cost the client gauges is ours too. That is
said HERE and in the repo's docs, not on the board (decided 2026-09-13: the
page carried a disclaimer line; the deployment did not want it there).
"""
import datetime
import hashlib
import io
import json
import os
import threading
import time

import fmowar
try:
    import fmosectors                        # the (selector, tile) -> sector row table
except ImportError:                          # pragma: no cover
    fmosectors = None

NAME = "fmo"
TITLE = "Front Mission Online - City Control"

#: NOT under services/fedata/ (restarts FE) and NOT a services/fmo*.py name
#: (pol-git-sync's fmo rule would restart the game server).
ART_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "boardart", "fmo")

OCU, USN = fmowar.OCU, fmowar.USN
NATION_NAME = {OCU: "O.C.U.", USN: "U.S.N.", 0: "Deadlock"}      # SE 30:19..21
#: zone KIND (selector // 100) -> the name its areas go by; kind 5 is the
#: frontline, "FZ-06 / FZ-10 / FZ-14" (SE's own update notes: "06/12 Maltaf")
KIND_NAME = {1: "O.C.U. Control Zone", 2: "O.C.U. Occupied Zone",
             3: "U.S.N. Control Zone", 4: "U.S.N. Occupied Zone", 6: "Coliseum"}

#: the sidebar's newest wins, across every sector
RECENT_MAX = 12
#: fmo.py's live marker; trusted for pol-git-sync's grace, as the Jan board does
LIVE_MARKER = "fmo-sessions-live.json"
LIVE_GRACE_S = float(os.environ.get("POL_DEPLOY_MATCH_GRACE_S", "900") or 900)

#: THE COLUMN MODEL, in the window's native 800x600: ONE x and ONE alignment
#: per column, shared by its header and its values, by the page AND by
#: render.png (the Discord image). Numbers right, text left.
LAYOUT = {
    "w": 800, "h": 600, "side_w": 240, "gap": 14, "pad": 14,
    "title": {"h": 38, "baseline": 27, "size": 23},
    "score": {"y": 46, "h": 72, "bar": [220, 580, 80, 12]},
    "clock": {"y": 124, "h": 20},
    "head": {"y": 152, "h": 22},
    "rows": {"y": 176, "pitch": 20, "n": 19},
    "status": {"y": 566, "h": 22},
    "cols": [{"key": "name", "x": 34, "align": "left", "label": "City Name"},
             {"key": "sector", "x": 196, "align": "left", "label": "Sector"},
             {"key": "control", "x": 322, "align": "left", "label": "Control"},
             {"key": "rate", "x": 522, "align": "right", "label": "Rate"},
             {"key": "bg", "x": 648, "align": "right", "label": "B.G.Cost"},
             {"key": "rank", "x": 762, "align": "right", "label": "Rank"}],
}

_SNAP = {"t": 0.0, "snap": None}
_SNAP_LOCK = threading.Lock()
_RENDER = {"sig": None, "png": None}
_RENDER_LOCK = threading.Lock()
_ROWS = {"d": None}


def data_dir():
    return os.environ.get("POL_DATA_DIR", "/data")


def war_path():
    """fmo.py's own rule (fmowar.STATE_PATH): $FMO_WAR_STATE, else the data
    volume's fmowar.json -- read at call time so a test can point it."""
    return os.environ.get("FMO_WAR_STATE", "").strip() or os.path.join(data_dir(), "fmowar.json")


def wins_path():
    return (os.environ.get("FMO_SECTOR_WINS", "").strip()
            or os.path.join(data_dir(), "fmo_sector_wins.json"))


def zone_label(selector):
    """505 -> 'FZ-06', 200 -> 'O.C.U. Occupied Zone 01'."""
    kind, idx = divmod(int(selector), 100)
    if kind == 5:
        return "FZ-%02d" % (idx + 1)
    if kind in KIND_NAME:
        return "%s %02d" % (KIND_NAME[kind], idx + 1)
    return "Zone %d" % int(selector)


def sector_row(selector, tile):
    """The sector's number in its zone (the ARE row: 'Sector 12'), from
    fmosectors' table, or None."""
    if _ROWS["d"] is None:
        d = {}
        for line in (getattr(fmosectors, "_TABLE", "") or "").splitlines():
            sel, _, rest = line.partition("|")
            for tok in rest.split():
                try:
                    t, r, _m = tok.split(":")
                    d[(int(sel), int(t))] = int(r)
                except ValueError:
                    continue
        _ROWS["d"] = d
    return _ROWS["d"].get((int(selector), int(tile)))


def load_war(path=None):
    """(War, present, mtime). War(autosave=False).load() only READS; a
    missing or unreadable file is an empty war."""
    path = path or war_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = None
    return fmowar.War(path=path, autosave=False), mtime is not None, mtime


def holder(s):
    """0 / OCU / USN for a sector record, by War.score's own rule: a
    deadlocked or missing sector is nobody's."""
    if not s or s.get("deadlock"):
        return 0
    n = s.get("nation")
    return n if n in (OCU, USN) else 0


def seed_holder(s):
    """Who a sector opened the war with: its seed kind's nation
    (fmowar.SEED_BY_KIND, "seeded": "kind 2"), nobody when it was not seeded."""
    try:
        kind = int(str(s.get("seeded") or "").split()[-1])
    except (ValueError, IndexError):
        return 0
    return fmowar.SEED_BY_KIND.get(kind, (0, 0))[0]


def changed_hands(s):
    """A sector whose holder is not the one it opened with. NOT its `updated`
    stamp: settle() stamps a win that only fills the counter (prod 2026-09-12:
    two O.C.U. Occupied Zone 01 sectors, still O.C.U.'s at 60 %)."""
    return holder(s) != seed_holder(s)


def cities(war):
    out = []
    for tile, (name, pts, where) in fmowar.CITIES.items():
        sel, row = (int(x) for x in where.split(":"))
        s = war.data.get("sectors", {}).get(str(tile))       # NEVER war.sector(): it inserts
        n = holder(s)
        s = s or {}
        wins = s.get("wins") or {}
        out.append({"name": name, "tile": tile, "zone": sel, "area": zone_label(sel),
                    "sector": row, "rank": pts, "nation": n,
                    "control": int(s.get("control") or 0) if n else 0,
                    "bg_max": int(s.get("bg_max") or 0), "bg_min": int(s.get("bg_min") or 0),
                    "wins": {"ocu": int(wins.get("1") or 0), "usn": int(wins.get("2") or 0)},
                    "updated": int(s.get("updated") or 0), "on_file": bool(s)})
    return out


def recent_battles(limit=RECENT_MAX, path=None):
    """[{zone, tile, nation, t, area, sector, city}], newest first, from the
    sector-win ledger. Missing file = no battles, never an error."""
    try:
        with open(path or wins_path(), encoding="utf-8") as fh:
            raw = json.load(fh) or {}
    except (OSError, ValueError):
        return []
    out = []
    for key, times in raw.items() if isinstance(raw, dict) else ():
        try:
            z, t, n = (int(x) for x in key.split(":"))
            stamps = [int(x) for x in times]
        except (ValueError, TypeError, AttributeError):
            continue
        city = fmowar.CITIES.get(t) if z // 100 == 5 else None
        for st in stamps:
            out.append({"zone": z, "tile": t, "nation": n, "t": st,
                        "area": zone_label(z), "sector": sector_row(z, t),
                        "city": city[0] if city else ""})
    out.sort(key=lambda b: -b["t"])
    return out[:limit]


def connections(now=None):
    """fmo.py's connection count while its stamp is fresh, 0 once stale, None
    with no marker (the page then claims nothing)."""
    try:
        with open(os.path.join(data_dir(), LIVE_MARKER), encoding="utf-8") as fh:
            d = json.load(fh) or {}
        stamp, count = float(d.get("stamp") or 0), int(d.get("count") or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    now = time.time() if now is None else now
    return count if 0 <= now - stamp < LIVE_GRACE_S else 0


def phase(war, now):
    n, start, judge, nxt, cease = fmowar.phase_at(now)
    phases = war.data.get("phases") or {}
    # tick() judges phase n once its judgement time passes (the ceasefire)
    judged = phases.get(str(n)) if cease else None
    last = phases.get(str(n - 1)) if n > 1 else None
    return {"n": n, "start": start, "judge": judge, "next": nxt, "ceasefire": bool(cease),
            "judged": judged, "last": last}


def _sig(snap):
    h = hashlib.sha1()
    h.update(repr([(c["tile"], c["nation"], c["control"], c["bg_max"], c["bg_min"])
                   for c in snap["cities"]]).encode())
    p = snap["phase"]
    h.update(repr((p["n"], p["ceasefire"], p["judged"], p["last"], snap["war"]["present"],
                   [(b["zone"], b["tile"], b["nation"], b["t"]) for b in snap["recent"]])).encode())
    return h.hexdigest()[:16]


def snapshot(args=None, now=None):
    now = time.time() if now is None else float(now)
    war, present, mtime = load_war()
    rows = cities(war)
    score = war.score()
    sectors = war.data.get("sectors") or {}
    changed = sum(1 for s in sectors.values() if isinstance(s, dict) and changed_hands(s))
    snap = {"board": NAME, "title": TITLE, "updated": int(now),
            "poll_s": float(getattr(args, "poll", 5.0) or 5.0),
            "war": {"present": present, "mtime": int(mtime) if mtime else None,
                    "sectors": len(sectors), "changed": changed},
            "phase": phase(war, now),
            "score": {"ocu": score[OCU], "usn": score[USN],
                      "total": sum(p for _n, p, _w in fmowar.CITIES.values())},
            "cities": rows,
            "recent": recent_battles(),
            "connections": connections(now),
            "defaults": {"counter_cap": fmowar.COUNTER_CAP, "rate_step": fmowar.RATE_STEP}}
    snap["sig"] = _sig(snap)
    return snap


def cached_snapshot(args=None, ttl=2.0):
    now = time.time()
    with _SNAP_LOCK:
        if _SNAP["snap"] is not None and now - _SNAP["t"] < ttl:
            return _SNAP["snap"]
    snap = snapshot(args, now)
    with _SNAP_LOCK:
        _SNAP.update(t=now, snap=snap)
    return snap


def art_files():
    """What the page may load: the backdrop and the web fonts. The .ttf
    copies (render.png's) and the licences are not served."""
    try:
        return frozenset(n for n in os.listdir(ART_DIR) if n.endswith((".png", ".woff2")))
    except OSError:
        return frozenset()


# ---------------------------------------------------------------------------
# the words the page and the image share
# ---------------------------------------------------------------------------
def _utc(t):
    return datetime.datetime.fromtimestamp(int(t), datetime.timezone.utc).strftime("%Y/%m/%d %H:%M UTC")


def clock_text(p):
    """The phase line without its live countdown."""
    if p["n"] == 0:
        return "No war yet   Phase 1 begins %s" % _utc(p["next"])
    if p["ceasefire"]:
        return "Phase %d ceasefire   Phase %d begins %s" % (p["n"], p["n"] + 1, _utc(p["next"]))
    return "Phase %d   Judged %s" % (p["n"], _utc(p["judge"]))


def status_text(snap):
    """What the status line says about the war itself."""
    if not snap["war"]["present"]:
        return "No war state on file yet: every city is Deadlock"
    held, n = sum(1 for c in snap["cities"] if c["nation"]), snap["war"]["changed"]
    if not n:
        return "No sector has changed hands this phase"
    if not held:
        return "No city held yet; %d sector%s changed hands" % (n, "" if n == 1 else "s")
    return "%d of %d cities held" % (held, len(snap["cities"]))


def cells(c):
    """One row's text, column by column (LAYOUT cols)."""
    return {"name": c["name"], "sector": "%s / %02d" % (c["area"], c["sector"]),
            "control": NATION_NAME[c["nation"]],
            "rate": ("%d%%" % c["control"]) if c["nation"] else "-",
            "bg": "%d / %d" % (c["bg_max"], c["bg_min"]), "rank": str(c["rank"])}


# ---------------------------------------------------------------------------
# render.png: the same window, drawn with Pillow for Discord
# ---------------------------------------------------------------------------
C_OCU, C_USN, C_DL = (98, 146, 255), (255, 110, 88), (170, 178, 182)
C_CYAN, C_TEXT, C_DIM = (95, 224, 207), (232, 238, 240), (150, 162, 166)
NATION_RGB = {OCU: C_OCU, USN: C_USN, 0: C_DL}


def _font(name, size):
    from PIL import ImageFont
    return ImageFont.truetype(os.path.join(ART_DIR, name), size)


def chamfer(w, h, c=14):
    """The panel's outline: top-left and bottom-right corners cut."""
    return [(c, 0), (w, 0), (w, h - c), (w - c, h), (0, h), (0, c)]


def render(snap):
    """PNG bytes of the City Control window, or None without Pillow or art."""
    try:
        from PIL import Image, ImageDraw
        bg = Image.open(os.path.join(ART_DIR, "backdrop.png")).convert("RGB")
        fonts = {k: _font(*v) for k, v in {
            "title": ("fmo-title.ttf", 23), "big": ("fmo-title.ttf", 40),
            "text": ("fmo-text.ttf", 14), "bold": ("fmo-text-bold.ttf", 14),
            "head": ("fmo-text-bold.ttf", 13), "small": ("fmo-text.ttf", 12),
            "nat": ("fmo-text-bold.ttf", 16)}.items()}
    except (ImportError, OSError):
        return None
    L = LAYOUT
    W, H = L["w"], L["h"]
    cv = bg.resize((W, W), Image.LANCZOS).crop((0, (W - H) // 2, W, (W + H) // 2)).convert("RGBA")
    cv.alpha_composite(Image.new("RGBA", (W, H), (6, 10, 12, 96)))
    panel = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(panel)
    d.polygon(chamfer(W - 1, H - 1), fill=(30, 34, 36, 214), outline=C_CYAN + (170,))
    d.rectangle([1, 1, W - 2, L["title"]["h"]], fill=(96, 102, 106, 235))
    d.polygon(chamfer(W - 1, H - 1), outline=C_CYAN + (170,))
    cv.alpha_composite(panel)
    # from here on what is drawn must BLEND: Draw(im, "RGBA") blends only onto
    # an RGB image (on RGBA it replaces the pixel, alpha and all)
    cv = cv.convert("RGB")
    d = ImageDraw.Draw(cv, "RGBA")

    def text(x, y, h, s, font, fill, align="left", stroke=0):
        d.text((x, y + h / 2), s, font=fonts[font], fill=fill,
               anchor={"left": "lm", "right": "rm", "center": "mm"}[align],
               stroke_width=stroke, stroke_fill=(22, 24, 26))

    T, S, p = L["title"], L["score"], snap["phase"]
    text(W / 2, 0, T["h"], "City Control", "title", (255, 255, 255), "center", 2)
    text(24, 0, T["h"], "Huffman Island", "head", (238, 242, 244))
    text(W - 24, 0, T["h"], "Phase %02d" % p["n"], "head", (238, 242, 244), "right")
    sc = snap["score"]
    text(34, S["y"], 22, "O.C.U.", "nat", C_OCU)
    text(34, S["y"] + 24, 44, str(sc["ocu"]), "big", (255, 255, 255))
    text(W - 34, S["y"], 22, "U.S.N.", "nat", C_USN, "right")
    text(W - 34, S["y"] + 24, 44, str(sc["usn"]), "big", (255, 255, 255), "right")
    text(W / 2, S["y"], 22, "Economic city ranks held", "small", C_DIM, "center")
    bx0, bx1, by, bh = S["bar"]
    span, tot = bx1 - bx0, max(1, sc["total"])
    xo = bx0 + span * sc["ocu"] / tot
    xu = bx1 - span * sc["usn"] / tot
    d.rectangle([bx0, by, bx1, by + bh], fill=C_DL + (90,))
    if sc["ocu"]:
        d.rectangle([bx0, by, xo, by + bh], fill=C_OCU + (230,))
    if sc["usn"]:
        d.rectangle([xu, by, bx1, by + bh], fill=C_USN + (230,))
    d.rectangle([bx0, by, bx1, by + bh], outline=(210, 216, 218, 120))
    dl = sc["total"] - sc["ocu"] - sc["usn"]
    text(W / 2, by + bh + 2, 20, "%d of %d points in Deadlock" % (dl, sc["total"]), "small",
         C_DIM, "center")
    text(W / 2, L["clock"]["y"], L["clock"]["h"], clock_text(p), "bold", C_CYAN, "center")
    hy, hh = L["head"]["y"], L["head"]["h"]
    d.rectangle([16, hy, W - 16, hy + hh], fill=(120, 126, 130, 110))
    for col in L["cols"]:
        text(col["x"], hy, hh, col["label"], "head", (255, 255, 255), col["align"])
    R = L["rows"]
    for i, c in enumerate(snap["cities"][:R["n"]]):
        y = R["y"] + R["pitch"] * i
        if i % 2:
            d.rectangle([16, y, W - 16, y + R["pitch"] - 1], fill=(255, 255, 255, 9))
        d.rectangle([18, y + 3, 20, y + R["pitch"] - 4], fill=NATION_RGB[c["nation"]] + (255,))
        cl = cells(c)
        for col in L["cols"]:
            k = col["key"]
            if k == "control":
                d.rectangle([col["x"], y + 5, col["x"] + 9, y + 14], fill=NATION_RGB[c["nation"]] + (255,))
                text(col["x"] + 16, y, R["pitch"], cl[k], "bold", NATION_RGB[c["nation"]])
            else:
                text(col["x"], y, R["pitch"], cl[k], "text", C_TEXT, col["align"])
    st = L["status"]
    text(24, st["y"], st["h"], status_text(snap), "bold", C_TEXT)
    text(W - 24, st["y"], st["h"], "Updated " + _utc(snap["updated"]), "small", C_DIM, "right")
    out = io.BytesIO()
    cv.convert("RGB").save(out, "PNG", optimize=True)
    return out.getvalue()


def render_cached(args=None):
    snap = cached_snapshot(args)
    with _RENDER_LOCK:
        if _RENDER["sig"] == snap["sig"] and _RENDER["png"] is not None:
            return _RENDER["png"]
    png = render(snap)
    with _RENDER_LOCK:
        _RENDER.update(sig=snap["sig"], png=png)
    return png


def route(path, query, args):
    """polboards' hook: /render.png, the Discord image."""
    if path != "/render.png":
        return None
    png = render_cached(args)
    if png is None:
        return 503, "the City Control art is not baked on this server", "text/plain", "no-store"
    return 200, png, "image/png", "no-cache"


# ---------------------------------------------------------------------------
# Discord (polboards' webhook): the window as the image, the score, who holds
# what, the newest battles; a short post when a city changes hands or a phase
# turns over
# ---------------------------------------------------------------------------
def _ago(t, now):
    s = max(0, now - t)
    return ("just now" if s < 90 else "%dm ago" % round(s / 60) if s < 3600
            else "%dh ago" % round(s / 3600) if s < 86400 else "%dd ago" % round(s / 86400))


def _battle_line(b, now):
    where = "%s / %02d" % (b["area"], b["sector"]) if b["sector"] else b["area"]
    return "`%s`%s: **%s** win, %s" % (where, (" " + b["city"]) if b["city"] else "",
                                     NATION_NAME.get(b["nation"], "?"), _ago(b["t"], now))


def discord_message(snap, args=None):
    """(payload, files) for the board's one Discord message."""
    sc, p = snap["score"], snap["phase"]
    if p["n"] == 0:
        when = "No war yet. Phase 1 begins <t:%d:f>." % p["next"]
    elif p["ceasefire"]:
        when = "Phase %d ceasefire. Phase %d begins <t:%d:f> (<t:%d:R>)." % (
            p["n"], p["n"] + 1, p["next"], p["next"])
    else:
        when = "Phase %d, judged <t:%d:f> (<t:%d:R>)." % (p["n"], p["judge"], p["judge"])
    lines = ["**O.C.U. %d : %d U.S.N.**  (%d economic-city points in play)"
             % (sc["ocu"], sc["usn"], sc["total"]), when, ""]
    for n in (OCU, USN):
        held = [c for c in snap["cities"] if c["nation"] == n]
        if held:
            lines.append("**%s** %s" % (NATION_NAME[n], ", ".join(
                "%s %d" % (c["name"], c["rank"]) for c in held)))
    dl = [c for c in snap["cities"] if not c["nation"]]
    if dl:
        lines.append("**Deadlock** %s" % ("all %d cities" % len(dl) if len(dl) == len(snap["cities"])
                                         else ", ".join(c["name"] for c in dl)))
    lines.append("*%s.*" % status_text(snap))
    if snap["recent"]:
        lines += ["", "**Recent battles**"] + [_battle_line(b, snap["updated"])
                                               for b in snap["recent"][:5]]
    color = (0x6292FF if sc["ocu"] > sc["usn"] else 0xFF6E58 if sc["usn"] > sc["ocu"]
             else 0x5FE0CF)
    embed = {"title": "Front Mission Online: City Control",
             "description": "\n".join(lines)[:4000], "color": color,
             "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(snap["updated"])),
             "footer": {"text": "Huffman Island"}}
    url = (getattr(args, "fmo_url", "") or "").strip()
    if url:
        embed["url"] = url
    payload = {"embeds": [embed], "allowed_mentions": {"parse": []}}
    png = render_cached(args)
    files = []
    if png is not None:
        embed["image"] = {"url": "attachment://fmo-city-control.png"}
        payload["attachments"] = [{"id": 0, "filename": "fmo-city-control.png"}]
        files.append(("fmo-city-control.png", "image/png", png))
    else:
        payload["attachments"] = []
    return payload, files


def discord_events(prev, snap):
    """A city changed hands; a phase was judged; a new phase began."""
    out = []
    was = {c["tile"]: c for c in prev["cities"]}
    for c in snap["cities"]:
        o = was.get(c["tile"])
        if o is None or o["nation"] == c["nation"]:
            continue
        where = "%s (%s / %02d)" % (c["name"], c["area"], c["sector"])
        if c["nation"]:
            out.append("\U0001F3D9 **%s** is now held by **%s**%s"
                       % (where, NATION_NAME[c["nation"]],
                          " (was %s)" % NATION_NAME[o["nation"]] if o["nation"] else ""))
        else:
            out.append("\U0001F3D9 **%s** fell into Deadlock (was %s)"
                       % (where, NATION_NAME[o["nation"]]))
    pp, p = prev["phase"], snap["phase"]
    if p["judged"] and not pp["judged"]:
        j = p["judged"]
        res = ("went to **%s**" % NATION_NAME[j["winner"]]) if j.get("winner") else "ended in a tie"
        out.append("\U0001F3C1 Phase %d %s, O.C.U. %d : %d U.S.N."
                   % (p["n"], res, j.get("ocu", 0), j.get("usn", 0)))
    if p["n"] > pp["n"] and p["n"] > 0:
        out.append("\U0001F4E3 Phase %d has begun" % p["n"])
    return out


# ---------------------------------------------------------------------------
# the page: the City Control WINDOW as a grey war-map panel on the zone's own
# satellite image, scaled whole to the browser; every word is real text on
# LAYOUT's one column model; on a 16:9 screen a second panel lists the newest
# battles and the connection count
# ---------------------------------------------------------------------------
_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Front Mission Online - City Control</title>
<style>
@font-face{font-family:"FmoText";src:url(art/fmo-text.woff2) format("woff2");font-weight:500;font-display:block}
@font-face{font-family:"FmoText";src:url(art/fmo-text-bold.woff2) format("woff2");font-weight:700;font-display:block}
@font-face{font-family:"FmoTitle";src:url(art/fmo-title.woff2) format("woff2");font-weight:700;font-display:block}
:root{--ocu:#6292ff;--usn:#ff6e58;--dl:#aab2b6;--cyan:#5fe0cf;--text:#e8eef0;--dim:#96a2a6}
html,body{margin:0;height:100%;overflow:hidden;background:#12181a}
/* the war map: the zone's satellite image, dimmed toward the edges, under a
   faint sector grid */
body{background:repeating-linear-gradient(0deg,rgba(95,224,207,.07) 0 1px,transparent 1px 48px),repeating-linear-gradient(90deg,rgba(95,224,207,.07) 0 1px,transparent 1px 48px),radial-gradient(ellipse at 50% 45%,rgba(8,12,14,.18) 0%,rgba(8,12,14,.55) 70%,rgba(4,6,8,.8) 100%),url(art/backdrop.png) 50% 50%/cover no-repeat,#1a2224}
#wrap{position:fixed;left:0;top:0;width:800px;height:600px;transform-origin:0 0;-webkit-user-select:none;user-select:none;filter:drop-shadow(0 6px 14px rgba(0,0,0,.6))}
.panel{position:absolute;top:0;height:600px;overflow:hidden;background:rgba(30,34,36,.86);clip-path:polygon(14px 0,100% 0,100% calc(100% - 14px),calc(100% - 14px) 100%,0 100%,0 14px)}
.panel>svg.frame{position:absolute;left:0;top:0;pointer-events:none;z-index:5}
#stage{left:0;width:800px}
#side{width:240px;font-family:"FmoText",sans-serif;color:var(--text)}
.bar{position:absolute;left:0;right:0;top:0;height:38px;background:linear-gradient(#80878b,#5e6468)}
.t{position:absolute;white-space:pre;font-family:"FmoText","M PLUS 1p",sans-serif;font-weight:500;font-variant-numeric:tabular-nums;color:var(--text);pointer-events:none}
.b{font-weight:700}
.title{font-family:"FmoTitle","FmoText",sans-serif;font-weight:700;color:#fff;letter-spacing:2px;-webkit-text-stroke:0;text-shadow:0 0 1px #16181a,1px 1px 0 #16181a,-1px -1px 0 #16181a,1px -1px 0 #16181a,-1px 1px 0 #16181a,0 2px 3px rgba(0,0,0,.5)}
.big{font-family:"FmoTitle","FmoText",sans-serif;font-weight:700;color:#fff}
.ocu{color:var(--ocu)}.usn{color:var(--usn)}.dl{color:var(--dl)}.dim{color:var(--dim)}.cyan{color:var(--cyan)}
.chip{position:absolute;width:10px;height:10px}
.acc{position:absolute;left:18px;width:3px}
.stripe{position:absolute;left:16px;right:16px;background:rgba(255,255,255,.035)}
#head{position:absolute;left:16px;right:16px;background:rgba(120,126,130,.45)}
#scorebar{position:absolute;overflow:hidden;background:rgba(170,178,182,.35);box-shadow:inset 0 0 0 1px rgba(210,216,218,.45)}
#scorebar i{position:absolute;top:0;bottom:0}
#recent{list-style:none;margin:0;padding:0 16px;position:absolute;top:50px;left:0;right:0}
#recent li{padding:6px 0 7px;border-bottom:1px solid rgba(150,162,166,.25)}
#recent .r1,#recent .r2{display:flex;justify-content:space-between;align-items:baseline;white-space:nowrap;gap:8px}
#recent .nm{font-weight:700;font-size:13px;overflow:hidden;text-overflow:ellipsis}
#recent .cy{font-size:12px;color:var(--dim);overflow:hidden;text-overflow:ellipsis}
#recent .r2{font-size:12px;color:var(--dim);font-variant-numeric:tabular-nums}
#recent .w{font-weight:700}
#recent .none{border:0;color:var(--dim);font-size:13px;text-align:center;padding-top:24px}
#side-foot{position:absolute;left:16px;right:16px;bottom:18px;text-align:center;font-size:13px;line-height:18px;color:var(--dim)}
#side-foot b{color:var(--text);font-weight:700}
#msg{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font:16px system-ui,sans-serif;color:var(--text)}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
</style></head><body>
<div id="wrap" aria-hidden="true"><div id="stage" class="panel"><div id="msg">Loading...</div></div><aside id="side" class="panel" hidden></aside></div>
<table class="sr" id="table"><caption id="cap">City Control</caption><thead><tr><th>City Name</th><th>Sector</th><th>Control</th><th>Rate</th><th>B.G.Cost</th><th>Rank</th></tr></thead><tbody id="rows"></tbody></table>
<script>
"use strict";
const L = /*LAYOUT*/null;
let S = null, SIG = '', OK = false;
const $ = s => document.querySelector(s);
const stage = $('#stage'), wrap = $('#wrap'), side = $('#side');
const E = {};
const NAT = {1: ['O.C.U.', 'ocu'], 2: ['U.S.N.', 'usn'], 0: ['Deadlock', 'dl']};
function el(tag, cls, parent){ const e = document.createElement(tag); if (cls) e.className = cls; (parent || stage).appendChild(e); return e; }
function box(e, x, y, w, h){ e.style.left = x + 'px'; e.style.top = y + 'px'; e.style.width = w + 'px'; e.style.height = h + 'px'; }
// one line of text at x, aligned left / right / center ON x, vertically
// centred in the band y..y+h
function at(e, text, x, align, y, h, size){
  e.textContent = text;
  e.style.fontSize = e.style.lineHeight = size + 'px';
  e.style.height = size + 'px';
  e.style.top = (y + Math.round((h - size) / 2)) + 'px';
  const w = e.offsetWidth;
  e.style.left = (align === 'right' ? x - w : align === 'center' ? x - w / 2 : x) + 'px';
}
function frame(panel, w){
  const NS = 'http://www.w3.org/2000/svg', s = document.createElementNS(NS, 'svg');
  s.setAttribute('class', 'frame'); s.setAttribute('width', w); s.setAttribute('height', L.h);
  const p = document.createElementNS(NS, 'polygon'), c = 14, h = L.h;
  p.setAttribute('points', [[c + .5, .5], [w - .5, .5], [w - .5, h - c], [w - c, h - .5], [.5, h - .5], [.5, c]].map(q => q.join(',')).join(' '));
  p.setAttribute('fill', 'none'); p.setAttribute('stroke', 'rgba(95,224,207,.65)'); p.setAttribute('stroke-width', '1');
  s.appendChild(p); panel.appendChild(s);
}
function build(){
  stage.innerHTML = '';
  el('div', 'bar');
  frame(stage, L.w);
  const T = L.title, Sc = L.score;
  E.title = el('span', 't title'); E.title.id = 'title';
  E.where = el('span', 't b'); E.phaseNo = el('span', 't b');
  E.ocuLabel = el('span', 't b ocu'); E.usnLabel = el('span', 't b usn');
  E.ocu = el('span', 't big'); E.ocu.id = 'score-ocu';
  E.usn = el('span', 't big'); E.usn.id = 'score-usn';
  E.held = el('span', 't dim');
  E.bar = el('div'); E.bar.id = 'scorebar';
  const [bx0, bx1, by, bh] = Sc.bar; box(E.bar, bx0, by, bx1 - bx0, bh);
  E.barO = el('i', '', E.bar); E.barO.style.background = 'var(--ocu)';
  E.barU = el('i', '', E.bar); E.barU.style.background = 'var(--usn)';
  E.dlText = el('span', 't dim');
  E.clock = el('span', 't b cyan'); E.clock.id = 'clock';
  const hd = el('div'); hd.id = 'head'; box(hd, 16, L.head.y, L.w - 32, L.head.h); hd.style.left = hd.style.right = '';
  E.hdr = {};
  for (const c of L.cols) E.hdr[c.key] = el('span', 't b');
  const R = L.rows;
  E.rows = [];
  for (let i = 0; i < R.n; i++){
    const y = R.y + R.pitch * i, row = {};
    if (i % 2){ const s = el('div', 'stripe'); s.style.top = y + 'px'; s.style.height = (R.pitch - 1) + 'px'; }
    row._acc = el('div', 'acc'); row._acc.style.top = (y + 3) + 'px'; row._acc.style.height = (R.pitch - 7) + 'px';
    row._chip = el('div', 'chip');
    for (const c of L.cols) row[c.key] = el('span', 't row');
    E.rows.push(row);
  }
  E.status = el('span', 't b'); E.status.id = 'status';
  E.live = el('span', 't dim'); E.live.id = 'live';
}
function buildSide(){
  side.innerHTML = '';
  el('div', 'bar', side);
  frame(side, L.side_w);
  const t = el('span', 't title', side); t.id = 'side-title';
  at(t, 'Recent Battles', L.side_w / 2, 'center', 0, L.title.h, 18);
  E.recent = el('ol', '', side); E.recent.id = 'recent';
  E.sideFoot = el('div', '', side); E.sideFoot.id = 'side-foot';
}
// the window alone, or with the battles panel when that costs the window
// less than a fifth of its size (a 16:9 screen, not a phone)
function fit(){
  const W = innerWidth, H = innerHeight, P = L.pad, G = L.gap, SW = L.side_w;
  const k1 = Math.min((W - 2 * P) / L.w, (H - 2 * P) / L.h);
  const k2 = Math.min((W - 2 * P) / (L.w + G + SW), (H - 2 * P) / L.h);
  const withSide = W >= 700 && k2 >= k1 * 0.8;
  const k = withSide ? k2 : k1, w = withSide ? L.w + G + SW : L.w;
  side.hidden = !withSide;
  side.style.left = (L.w + G) + 'px';
  wrap.style.width = w + 'px';
  wrap.style.transform = 'scale(' + k + ')';
  wrap.style.left = ((W - w * k) / 2) + 'px';
  wrap.style.top = ((H - L.h * k) / 2) + 'px';
}
function two(n){ return (n < 10 ? '0' : '') + n; }
// the VIEWER's local time, with its zone's name (decided 2026-09-13); the
// server judges at 12:00 UTC, so a US viewer reads 07:00 EST on 11/01
function when(t){
  const d = new Date(t * 1000);
  let z = '';
  try { z = new Intl.DateTimeFormat('en-US', {timeZoneName: 'short'}).formatToParts(d).find(p => p.type === 'timeZoneName').value; } catch (e) {}
  return d.getFullYear() + '/' + two(d.getMonth() + 1) + '/' + two(d.getDate()) + ' ' + two(d.getHours()) + ':' + two(d.getMinutes()) + (z ? ' ' + z : '');
}
function left(t){
  let s = Math.max(0, Math.floor(t - Date.now() / 1000));
  const d = Math.floor(s / 86400); s -= d * 86400;
  const h = Math.floor(s / 3600); s -= h * 3600;
  const m = Math.floor(s / 60);
  return (d ? d + 'd ' : '') + (d || h ? h + 'h ' : '') + m + 'm left';
}
function clockText(){
  const p = S.phase;
  if (p.n === 0) return 'No war yet   Phase 1 begins ' + when(p.next) + '   ' + left(p.next);
  if (p.ceasefire) return 'Phase ' + p.n + ' ceasefire   Phase ' + (p.n + 1) + ' begins ' + when(p.next) + '   ' + left(p.next);
  return 'Phase ' + p.n + '   Judged ' + when(p.judge) + '   ' + left(p.judge);
}
function statusText(){
  if (!S.war.present) return 'No war state on file yet: every city is Deadlock';
  const held = S.cities.filter(c => c.nation).length, n = S.war.changed;
  if (!n) return 'No sector has changed hands this phase';
  if (!held) return 'No city held yet; ' + n + (n === 1 ? ' sector' : ' sectors') + ' changed hands';
  return held + ' of ' + S.cities.length + ' cities held';
}
function cells(c){
  return {name: c.name, sector: c.area + ' / ' + two(c.sector), control: NAT[c.nation][0],
          rate: c.nation ? c.control + '%' : '-', bg: c.bg_max + ' / ' + c.bg_min, rank: String(c.rank)};
}
function paintLive(){
  if (!S || !E.live) return;
  const t = new Date();
  at(E.live, OK ? 'Live ' + two(t.getHours()) + ':' + two(t.getMinutes()) + ':' + two(t.getSeconds()) : 'Reconnecting...',
     L.w - 24, 'right', L.status.y, L.status.h, 12);
  at(E.clock, clockText(), L.w / 2, 'center', L.clock.y, L.clock.h, 14);
}
function show(){
  if (!S) return;
  const T = L.title, Sc = L.score, p = S.phase, sc = S.score;
  at(E.title, 'City Control', L.w / 2, 'center', 0, T.h, T.size);
  at(E.where, 'Huffman Island', 24, 'left', 0, T.h, 13);
  at(E.phaseNo, 'Phase ' + two(p.n), L.w - 24, 'right', 0, T.h, 13);
  at(E.ocuLabel, 'O.C.U.', 34, 'left', Sc.y, 22, 16);
  at(E.usnLabel, 'U.S.N.', L.w - 34, 'right', Sc.y, 22, 16);
  at(E.ocu, String(sc.ocu), 34, 'left', Sc.y + 24, 44, 40);
  at(E.usn, String(sc.usn), L.w - 34, 'right', Sc.y + 24, 44, 40);
  at(E.held, 'Economic city ranks held', L.w / 2, 'center', Sc.y, 22, 12);
  const [bx0, bx1, by, bh] = Sc.bar, span = bx1 - bx0, tot = Math.max(1, sc.total);
  E.barO.style.left = '0'; E.barO.style.width = (span * sc.ocu / tot) + 'px';
  E.barU.style.right = '0'; E.barU.style.width = (span * sc.usn / tot) + 'px';
  at(E.dlText, (sc.total - sc.ocu - sc.usn) + ' of ' + sc.total + ' points in Deadlock', L.w / 2, 'center', by + bh + 2, 20, 12);
  for (const c of L.cols) at(E.hdr[c.key], c.label, c.x, c.align, L.head.y, L.head.h, 13);
  const R = L.rows;
  E.rows.forEach((row, i) => {
    const c = S.cities[i], y = R.y + R.pitch * i;
    const cl = c ? cells(c) : {}, cls = c ? NAT[c.nation][1] : 'dl';
    row._acc.style.background = row._chip.style.background = 'var(--' + cls + ')';
    row._acc.hidden = row._chip.hidden = !c;
    for (const col of L.cols){
      const e = row[col.key];
      if (col.key === 'control'){
        box(row._chip, col.x, y + 5, 10, 10);
        e.className = 't row b ' + cls;
        at(e, cl.control || '', col.x + 16, 'left', y, R.pitch, 14);
      } else at(e, cl[col.key] || '', col.x, col.align, y, R.pitch, 14);
    }
  });
  at(E.status, statusText(), 24, 'left', L.status.y, L.status.h, 13);
  paintLive();
  paintSide();
  const tb = $('#rows'); tb.innerHTML = '';
  for (const c of S.cities){
    const tr = document.createElement('tr'), v = cells(c);
    for (const k of ['name', 'sector', 'control', 'rate', 'bg', 'rank']){ const td = document.createElement('td'); td.textContent = v[k]; tr.appendChild(td); }
    tb.appendChild(tr);
  }
  $('#cap').textContent = 'City Control: O.C.U. ' + sc.ocu + ', U.S.N. ' + sc.usn + '. ' + clockText();
}
function ago(t){
  const s = Math.max(0, Date.now() / 1000 - t);
  return s < 90 ? 'just now' : s < 3600 ? Math.round(s / 60) + 'm ago'
       : s < 86400 ? Math.round(s / 3600) + 'h ago' : Math.round(s / 86400) + 'd ago';
}
function paintSide(){
  if (!S || !E.recent) return;
  const span = (cls, text) => { const e = document.createElement('span'); e.className = cls; e.textContent = text; return e; };
  E.recent.innerHTML = '';
  if (!S.recent.length){ const li = document.createElement('li'); li.className = 'none'; li.textContent = 'No battles yet.'; E.recent.appendChild(li); }
  for (const b of S.recent){
    const li = document.createElement('li'), a = document.createElement('div'), c = document.createElement('div');
    a.className = 'r1'; c.className = 'r2';
    a.append(span('nm', b.sector ? b.area + ' / ' + two(b.sector) : b.area), span('cy', b.city || ''));
    const n = NAT[b.nation] || ['?', 'dl'];
    c.append(span('w ' + n[1], n[0] + ' win'), span('ago', ago(b.t)));
    li.append(a, c);
    E.recent.appendChild(li);
  }
  E.sideFoot.innerHTML = '';
  // the count as what it is, and no caveat line under it (decided
  // 2026-09-13); no marker = say nothing
  const n = S.connections;
  if (n != null){
    const bold = document.createElement('b');
    bold.textContent = n + (n === 1 ? ' connection' : ' connections') + ' to FMO';
    E.sideFoot.appendChild(bold);
  }
  // only as many battles as fit above the footer
  const limit = E.sideFoot.offsetTop - 6;
  while (E.recent.children.length > 1){
    const last = E.recent.lastElementChild;
    if (E.recent.offsetTop + last.offsetTop + last.offsetHeight <= limit) break;
    last.remove();
  }
}
addEventListener('resize', fit);
async function poll(){
  try {
    const r = await fetch('state.json', {cache: 'no-store'});
    if (r.ok){ S = await r.json(); OK = true; if (S.sig !== SIG || !E.done){ show(); E.done = true; } SIG = S.sig; }
    else OK = false;
  } catch (e) { OK = false; }
  paintLive(); paintSide();
  setTimeout(poll, ((S && S.poll_s) || 5) * 1000);
}
async function init(){
  fit();
  try { await Promise.all([document.fonts.load('500 14px "FmoText"', 'Aa1'), document.fonts.load('700 14px "FmoText"', 'Aa1'),
                           document.fonts.load('700 23px "FmoTitle"', 'City Control')]); } catch (e) {}
  build(); buildSide(); fit(); poll();
  setInterval(paintLive, 1000);
}
init();
</script></body></html>
"""
PAGE = _PAGE.replace("/*LAYOUT*/null", json.dumps(LAYOUT, separators=(",", ":")))
