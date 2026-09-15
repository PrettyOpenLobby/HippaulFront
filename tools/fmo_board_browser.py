#!/usr/bin/env python3
"""fmo_board_browser.py -- drive the FMO City Control page in a real headless
Chrome (render-web-ui-before-shipping), over a temporary war with three held
cities and a few battles, then over NO war file at all.

    python tools/fmo_board_browser.py [--shots DIR]

Fails on any JavaScript error, on a font or the backdrop that never loads, on
text that runs into its neighbour or out of the window, on a header that is
not on its values' alignment, or on a layout that does not fit a phone, a 4:3
and a 16:9 screen. Needs Chrome/Edge and websocket-client (fe_panel_browser.py).
"""
import argparse
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, os.pardir, "services"))

from fe_panel_browser import Browser, free_port   # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=tempfile.mkdtemp(prefix="fmo-board-shots-"))
    o = ap.parse_args(argv)
    os.makedirs(o.shots, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="fmo-board-browser-")
    data = os.path.join(tmp, "data")
    empty = os.path.join(tmp, "empty")
    os.makedirs(data)
    os.makedirs(empty)
    os.environ["POL_DATA_DIR"] = data
    os.environ.pop("FMO_WAR_STATE", None)
    import fmowar
    import boardfmo
    import polboards
    now = time.time()
    w = fmowar.War(path=os.path.join(data, "fmowar.json"))
    w.seed_from_sectors({505: {85102: 0}, 509: {94101: 0}, 513: {112101: 0}}, force=True)
    mid = fmowar._ts(2026, 10, 1)
    for _ in range(3):
        w.settle(85102, fmowar.OCU, won=True, now=mid)
        w.settle(112101, fmowar.USN, won=True, now=mid)
        w.settle(94101, fmowar.OCU, won=True, now=mid)
    wins = {"505:85102:1": [int(now) - 600 * i for i in range(4)],
            "513:112101:2": [int(now) - 450, int(now) - 5000],
            "200:69118:2": [int(now) - 90000], "509:103102:1": [int(now) - 7200]}
    with open(os.path.join(data, "fmo_sector_wins.json"), "w") as fh:
        json.dump(wins, fh)
    with open(os.path.join(data, "fmo-sessions-live.json"), "w") as fh:
        json.dump({"count": 2, "stamp": now}, fh)
    port = free_port()
    args = polboards.build_parser().parse_args(["--fmo-port", str(port)])
    srv = polboards.serve(boardfmo, args, port)
    url = "http://127.0.0.1:%d/" % port
    fails = []

    def check(name, ok, detail=""):
        print("  %-66s %s%s" % (name, "PASS" if ok else "FAIL",
                                "  " + str(detail) if detail and not ok else ""))
        if not ok:
            fails.append(name)

    # every visible text on the window, as [left, top, right, bottom, text]
    texts = """[...stage.querySelectorAll('.t')].filter(e => e.textContent.trim())
      .map(e => [e.offsetLeft, e.offsetTop, e.offsetLeft + e.offsetWidth, e.offsetTop + e.offsetHeight, e.textContent])"""
    overlap = """(() => { const r = %s, bad = [];
      for (let i = 0; i < r.length; i++) for (let j = i + 1; j < r.length; j++){
        const a = r[i], b = r[j];
        if (a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3]) bad.push(a[4] + '|' + b[4]); }
      return bad; })()""" % texts
    inside = """(() => %s.filter(r => r[0] < 16 || r[2] > L.w - 16 || r[1] < 0 || r[3] > L.h).map(r => r[4]))()""" % texts
    aligned = """(() => { const bad = [];
      const edge = (e, a) => a === 'left' ? e.offsetLeft : e.offsetLeft + e.offsetWidth;
      for (const c of L.cols){
        const h = E.hdr[c.key], hx = edge(h, c.align);
        for (const row of E.rows){
          const v = c.key === 'control' ? row._chip : row[c.key];
          const vx = c.key === 'control' ? v.offsetLeft : edge(v, c.align);
          if ((c.key === 'control' || v.textContent) && Math.abs(hx - vx) > 1) { bad.push(c.key + ' ' + hx + ' vs ' + vx); break; }
        } }
      return bad; })()"""
    fonts = ("document.fonts.check('500 14px \"FmoText\"') && document.fonts.check('700 14px \"FmoText\"')"
             " && document.fonts.check('700 23px \"FmoTitle\"')")
    backdrop = ("performance.getEntriesByType('resource').some(r => /art\\/backdrop\\.png$/.test(r.name)"
                " && r.responseEnd > 0)")

    b = Browser(width=1280, height=960)
    try:
        # the clock is in the VIEWER's zone: New York, where 11/01 is also the
        # day the clocks fall back (12:00 UTC = 07:00 EST, not 08:00 EDT)
        b.call("Emulation.setTimezoneOverride", timezoneId="America/New_York")
        b.goto(url, settle=3.0)
        check("the page has the war and its layout", b.js("!!(S && L && S.cities.length === 19)"))
        check("both text weights and the title face loaded", b.js(fonts))
        check("the satellite backdrop loaded", b.js(backdrop))
        check("the title is TEXT: City Control, spaced", b.js("E.title.textContent") == "City Control"
              and b.js("getComputedStyle(E.title).letterSpacing") == "2px")
        check("row 1 is Maltaf, held by O.C.U. at FZ-06 / 12",
              b.js("[E.rows[0].name.textContent, E.rows[0].control.textContent, E.rows[0].sector.textContent]")
              == ["Maltaf", "O.C.U.", "FZ-06 / 12"])
        check("the score reads 7 : 4, the bar split to match",
              b.js("[E.ocu.textContent, E.usn.textContent]") == ["7", "4"]
              and abs(b.js("E.barO.offsetWidth") - 360 * 7 / 66) < 1.5
              and abs(b.js("E.barU.offsetWidth") - 360 * 4 / 66) < 1.5,
              b.js("[E.barO.offsetWidth, E.barU.offsetWidth]"))
        check("the phase clock is live, in the viewer's local time",
              "Judged 2026/11/01 07:00 EST" in b.js("E.clock.textContent")
              and "left" in b.js("E.clock.textContent"), b.js("E.clock.textContent"))
        check("the status and the live line are INSIDE the window",
              b.js("E.status.textContent") == "3 of 19 cities held"
              and b.js("E.live.textContent").startswith("Live "), b.js("[E.status.textContent, E.live.textContent]"))
        check("every column header lines up with its values", b.js(aligned) == [], b.js(aligned))
        check("no text runs into another", b.js(overlap) == [], b.js(overlap))
        check("no text leaves the window", b.js(inside) == [], b.js(inside))
        check("no sidebar at 4:3 (1280x960)", b.js("side.hidden"))
        check("the hidden table mirrors the screen for screen readers",
              b.js("document.querySelectorAll('#rows tr').length") == 19)
        b.screenshot(os.path.join(o.shots, "1-city-control.png"))
        b.call("Emulation.setDeviceMetricsOverride", width=1920, height=1080, deviceScaleFactor=1, mobile=False)
        b.goto(url, settle=2.5)
        check("a 16:9 screen gets the battles panel beside the window",
              b.js("!side.hidden && side.getBoundingClientRect().left > stage.getBoundingClientRect().right"))
        check("...newest battle first: FZ-06 / 12 Maltaf, an O.C.U. win",
              b.js("[...document.querySelectorAll('#recent li')[0].querySelectorAll('span')].map(e => e.textContent)")[:3]
              == ["FZ-06 / 12", "Maltaf", "O.C.U. win"],
              b.js("document.querySelector('#recent li').textContent"))
        check("...none running into the footer, which counts CONNECTIONS",
              b.js("(() => { const l = document.querySelector('#recent li:last-child').getBoundingClientRect(),"
                   " f = E.sideFoot.getBoundingClientRect(); return l.bottom <= f.top })()")
              and b.js("E.sideFoot.textContent") == "2 connections to FMO", b.js("E.sideFoot.textContent"))
        b.screenshot(os.path.join(o.shots, "2-wide.png"))
        b.call("Emulation.setDeviceMetricsOverride", width=420, height=900, deviceScaleFactor=2, mobile=True)
        b.goto(url, settle=2.5)
        check("a phone gets the window alone, no sideways scroll",
              b.js("side.hidden") and b.js("document.documentElement.scrollWidth <= innerWidth + 1"))
        b.screenshot(os.path.join(o.shots, "3-phone.png"))
        # no war file, no ledger, no marker: the honest empty board
        os.environ["POL_DATA_DIR"] = empty
        boardfmo._SNAP.update(t=0.0, snap=None)
        b.call("Emulation.setDeviceMetricsOverride", width=1920, height=1080, deviceScaleFactor=1, mobile=False)
        b.goto(url, settle=2.5)
        check("with no war file every city is Deadlock and the board says why",
              b.js("S.cities.every(c => c.nation === 0)")
              and b.js("E.status.textContent").startswith("No war state on file yet")
              and b.js("[E.ocu.textContent, E.usn.textContent]") == ["0", "0"])
        check("...no battles yet, and no connection claim",
              b.js("document.querySelector('#recent .none').textContent") == "No battles yet."
              and b.js("E.sideFoot.textContent") == "")
        check("...still with no overlap", b.js(overlap) == [], b.js(overlap))
        b.screenshot(os.path.join(o.shots, "4-empty.png"))
        check("no JavaScript errors", not b.errors, b.errors)
    finally:
        b.close()
        srv.shutdown()
    print("screenshots in %s" % o.shots)
    if fails:
        raise SystemExit("[fmo_board_browser] %d FAILED: %s" % (len(fails), ", ".join(fails)))
    print("[fmo_board_browser] OK")


if __name__ == "__main__":
    main()
