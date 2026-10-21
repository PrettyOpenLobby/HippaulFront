#!/usr/bin/env python3
"""fmoreport.py -- the Huffman Conflict Report: each war phase's result as a web
page, the way SE posted it (topics 060411, 060612, 060815, 061016, 061219), served
by the City Control board at /report (boardfmo.route).

SE's reports were written by a fictional war correspondent and carried, per
phase, the dates, the result in control points (SE's own count: the nineteen
economic cities, 2 to 4 points each, 66 in all), and from Phase 01 a table of
every city with the side that held it at the judgement (060612:30-114); a
deadlocked city scored for nobody (Phase 03, Oak Hills: 36 + 27 = 63 of 66).
Each closed with the reward series and, for the loser, the fortress near
Freedom starting the next phase in Deadlock (guide/phase.html:55-64).

What this page shows, all from the stored war (fmowar.read_state; the page
never ticks or writes it): every judged phase newest first with that table,
the totals, the penalty and the reward, the phase in progress with the live
standing, and the Clash of Iron standings from the stored Coliseum document
(fmoarena.read_state). A phase judged before 2026-10-08 has no per-city record
(fmowar.War.judge started keeping one then) and shows its totals only. The
correspondent's prose and the soldiers' voices are not invented here.
"""
import html
import time

import fmoarena
import fmowar

NAT = {fmowar.OCU: ("O.C.U.", "ocu"), fmowar.USN: ("U.S.N.", "usn"), 0: ("Deadlock", "dl")}


def _esc(s):
    return html.escape(str(s), quote=True)


def _day(t):
    return time.strftime("%Y-%m-%d", time.gmtime(int(t))) if t else "-"


def _when(t):
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(int(t))) if t else "-"


def _nation(n):
    name, cls = NAT.get(int(n or 0), NAT[0])
    return '<span class="%s">%s</span>' % (cls, name)


def city_table(holders):
    """The 19-city table. `holders` = {tile: nation 0/1/2}. Returns (html,
    ocu points, usn points)."""
    rows, pts = [], {fmowar.OCU: 0, fmowar.USN: 0}
    for tile, (name, p, _where) in sorted(fmowar.CITIES.items(), key=lambda kv: (-kv[1][1], kv[1][0])):
        n = int(holders.get(str(tile), holders.get(tile, 0)) or 0)
        if n in pts:
            pts[n] += p
        rows.append("<tr><td>%s</td><td class=\"num\">%d</td><td>%s</td>"
                    "<td class=\"num ocu side\">%s</td><td class=\"num usn side\">%s</td></tr>"
                    % (_esc(name), p, _nation(n), p if n == fmowar.OCU else "",
                       p if n == fmowar.USN else ""))
    body = ("<table><thead><tr><th>City</th><th class=\"num\">Points</th><th>Held by</th>"
            "<th class=\"num side\">O.C.U.</th><th class=\"num side\">U.S.N.</th></tr></thead><tbody>"
            + "".join(rows)
            + "<tr class=\"tot\"><td>Total</td><td class=\"num\">%d</td><td></td>"
              "<td class=\"num ocu side\">%d</td><td class=\"num usn side\">%d</td></tr></tbody></table>"
            % (sum(p for _n, p, _w in fmowar.CITIES.values()), pts[fmowar.OCU], pts[fmowar.USN]))
    return body, pts[fmowar.OCU], pts[fmowar.USN]


def result_line(rec):
    o, u = int(rec.get("ocu") or 0), int(rec.get("usn") or 0)
    w = int(rec.get("winner") or 0)
    if w in (fmowar.OCU, fmowar.USN):
        hi, lo = (o, u) if w == fmowar.OCU else (u, o)
        return "%s wins, %d points to %d." % (_nation(w), hi, lo)
    return "A draw, %d points each. Both sides are rewarded and no side is penalised." % o


def phase_section(n, rec, start, war):
    """One judged phase."""
    out = ['<section class="phase"><h2>Phase %02d</h2>' % n,
           '<p class="dates">%s to %s (judged %s)</p>'
           % (_day(start), _day(rec.get("judged_at")), _when(rec.get("judged_at"))),
           '<p class="result">%s</p>' % result_line(rec)]
    if isinstance(rec.get("cities"), dict):
        out.append(city_table(rec["cities"])[0])
    else:
        out.append('<p class="dim">No city-by-city record was kept for this phase.</p>')
    pen = rec.get("penalty") or {}
    if pen.get("nation") in (fmowar.OCU, fmowar.USN):
        out.append("<p>The %s fortress near Freedom starts Phase %02d in Deadlock.</p>"
                   % (_nation(pen["nation"]), n + 1))
    for nat, r in sorted((rec.get("reward") or {}).items()):
        if isinstance(r, dict) and r.get("name"):
            out.append("<p>From Phase %02d (%s), %s pilots may buy the %s series at the "
                       "hangar.</p>" % (n + 1, _day(rec.get("reward_from")), _nation(int(nat)),
                                        _esc(r["name"])))
    out.append("</section>")
    return "".join(out)


def current_section(war, now):
    n, start, judge, nxt, cease = fmowar.phase_at(now, war.phase1())
    if n == 0:
        return ('<section class="phase"><h2>Before the war</h2><p>Phase 01 begins %s.</p>'
                "</section>" % _when(nxt))
    if cease:
        return ('<section class="phase"><h2>Ceasefire</h2><p>Phase %02d was judged %s. '
                "Phase %02d begins %s.</p></section>" % (n, _when(judge), n + 1, _when(nxt)))
    holders = {}
    for tile in fmowar.CITIES:
        s = (war.data.get("sectors") or {}).get(str(tile))
        holders[str(tile)] = (0 if not s or s.get("deadlock")
                              else int(s.get("nation") or 0))
    table, o, u = city_table(holders)
    return ('<section class="phase now"><h2>Phase %02d, in progress</h2>'
            '<p class="dates">Began %s, judged %s</p>'
            '<p class="result">Standing now: %s %d, %s %d.</p>%s</section>'
            % (n, _day(start), _when(judge), _nation(fmowar.OCU), o, _nation(fmowar.USN), u,
               table))


def coi_section(doc, now):
    """The Clash of Iron standings: each season with a stored night."""
    coi = (doc or {}).get("coi") or {}
    if not coi:
        return ""
    out = ['<section class="phase"><h2>Clash of Iron</h2>']
    for s in fmoarena.COI_SEASONS:
        sd = coi.get(s["name"])
        if not sd:
            continue
        reg = fmoarena.REGULATIONS.get(s.get("regulation"), {}).get("title", "")
        out.append("<h3>Season %s%s</h3>" % (_esc(s["name"]), (" - " + _esc(reg)) if reg else ""))
        quals = {a for a, _nm, _d in fmoarena.coi_qualifiers(sd)}
        for date in sorted(sd.get("nights") or {}):
            rows = fmoarena.coi_standings(sd["nights"][date])[:10]
            if not rows:
                continue
            out.append("<p class=\"dates\">Preliminary, %s</p><table><thead><tr><th>#</th>"
                       "<th>Pilot</th><th class=\"num\">Wins</th></tr></thead><tbody>" % _esc(date))
            for i, (a, name, wins, _t) in enumerate(rows):
                out.append("<tr%s><td class=\"num\">%d</td><td>%s</td><td class=\"num\">%d</td></tr>"
                           % (' class="q"' if a in quals and i == 0 else "", i + 1, _esc(name), wins))
            out.append("</tbody></table>")
        fin = sd.get("final") or {}
        if fin:
            names = {}
            for nights in (sd.get("nights") or {}).values():
                for a, r in nights.items():
                    names[a] = r.get("name", "")
            out.append("<p class=\"dates\">Final (H$%s per win)</p><table><tbody>"
                       % format(fmoarena.COI_PRIZE_PER_WIN, ","))
            for a, w in sorted(fin.items(), key=lambda kv: -int(kv[1])):
                out.append("<tr><td>%s</td><td class=\"num\">%d win(s)</td></tr>"
                           % (_esc(names.get(a, "")), int(w)))
            out.append("</tbody></table>")
    out.append("</section>")
    return "".join(out) if len(out) > 2 else ""


def page(war=None, coliseum_doc=None, now=None):
    """The whole report page (str)."""
    now = time.time() if now is None else float(now)
    if war is None:
        war = fmowar.War(autosave=False)
    phases = war.data.get("phases") or {}
    parts = [current_section(war, now)]
    for n in sorted((int(k) for k in phases), reverse=True):
        start = war._start_of(n) if hasattr(war, "_start_of") else None
        parts.append(phase_section(n, phases[str(n)], start, war))
    if coliseum_doc is None:
        coliseum_doc = fmoarena.read_state()
    parts.append(coi_section(coliseum_doc, now))
    return PAGE.replace("<!--BODY-->", "".join(parts)).replace(
        "<!--WHEN-->", _esc(_when(now)))


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Huffman Conflict Report</title>
<style>
@font-face{font-family:"FmoText";src:url(art/fmo-text.woff2) format("woff2");font-weight:500}
@font-face{font-family:"FmoText";src:url(art/fmo-text-bold.woff2) format("woff2");font-weight:700}
@font-face{font-family:"FmoTitle";src:url(art/fmo-title.woff2) format("woff2");font-weight:700}
:root{--ocu:#6292ff;--usn:#ff6e58;--dl:#aab2b6;--cyan:#5fe0cf;--text:#e8eef0;--dim:#96a2a6;--panel:rgba(30,34,36,.9)}
html,body{margin:0;background:#12181a;color:var(--text);font-family:"FmoText","M PLUS 1p",system-ui,sans-serif}
body{background:radial-gradient(ellipse at 50% 0%,rgba(8,12,14,.2) 0%,rgba(4,6,8,.85) 80%),url(art/backdrop.png) 50% 0/cover fixed no-repeat,#1a2224}
*{box-sizing:border-box}
main{max-width:760px;margin:0 auto;padding:24px 16px 48px;overflow-wrap:anywhere}
h1{font-family:"FmoTitle","FmoText",sans-serif;letter-spacing:1px;font-size:clamp(20px,6.5vw,28px);margin:0 0 4px}
h2{font-family:"FmoTitle","FmoText",sans-serif;font-size:20px;margin:0 0 6px;color:var(--cyan)}
h3{font-size:16px;margin:16px 0 4px}
.sub{color:var(--dim);margin:0 0 20px;font-size:14px}
.phase{background:var(--panel);padding:16px;margin:0 0 16px;clip-path:polygon(12px 0,100% 0,100% calc(100% - 12px),calc(100% - 12px) 100%,0 100%,0 12px)}
.dates{color:var(--dim);font-size:13px;margin:0 0 6px}
.result{font-weight:700;margin:0 0 10px}
.dim{color:var(--dim)}
table{width:100%;border-collapse:collapse;font-size:14px;font-variant-numeric:tabular-nums;margin:6px 0 10px}
th{text-align:left;color:var(--dim);font-weight:500;border-bottom:1px solid rgba(150,162,166,.35);padding:4px 6px}
td{padding:4px 6px;border-bottom:1px solid rgba(150,162,166,.15)}
.num{text-align:right}
.tot td{font-weight:700;border-bottom:0}
tr.q td{color:var(--cyan);font-weight:700}
.ocu{color:var(--ocu)}.usn{color:var(--usn)}.dl{color:var(--dl)}
p{margin:6px 0;line-height:1.45}
@media (max-width:480px){table{font-size:13px}th,td{padding:4px 3px}.side{display:none}.phase{padding:12px}}
</style></head><body><main>
<h1>Huffman Conflict Report</h1>
<p class="sub">The war on Huffman Island, phase by phase. As of <!--WHEN-->.</p>
<!--BODY-->
</main></body></html>
"""


if __name__ == "__main__":
    print(page())
