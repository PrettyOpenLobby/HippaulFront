#!/usr/bin/env python3
"""Drive the FMO lobby NPC editor in a REAL browser and check what it does.

Same discipline as fe_panel_browser.py, for the same reason: a page that
"renders" is not a page that works. This starts fmodevtool on loopback with
prod's real HQ roster as "what is served", the shipped map-102 floor plan and
a temp layout file, opens it in headless Chrome, and asserts EFFECTS -- a
click on the floor followed by "place" puts a row in the layout file with the
floor height read off a box; a drag moves that row; a drag on the handle
turns it; the void is refused; import copies nine rows.

Needs Chrome and `websocket-client`. Nothing here touches prod.

    python tools/fmo_panel_browser.py            # checks + screenshots
    python tools/fmo_panel_browser.py --shots D  # screenshots into D
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, "services"))
sys.path.insert(0, HERE)

from fe_panel_browser import Browser, free_port  # noqa: E402


def main(argv):
    import fedevtool
    import fmodevtool
    import fmolayout
    shots = None
    if "--shots" in argv:
        shots = argv[argv.index("--shots") + 1]
        os.makedirs(shots, exist_ok=True)
    fails = []

    def check(name, ok, detail=""):
        print("  %-70s %s" % (name, "PASS" if ok else "FAIL"))
        if not ok:
            fails.append("%s  %s" % (name, detail))

    def shot(b, name, sel=None):
        # A plain viewport capture, NOT fe_panel_browser.screenshot's clipped
        # one: captureBeyondViewport resizes the viewport for the clip and
        # restores it some time AFTER the call returns, so every canvas rect
        # measured for a while afterwards was the clip's layout (~10 px left
        # of the real one) and each aim landed a metre east. The map card sits
        # at the top of this page, so the 1500 px viewport holds it whole.
        if shots:
            import base64
            data = b.call("Page.captureScreenshot", format="png")["data"]
            with open(os.path.join(shots, name + ".png"), "wb") as fh:
                fh.write(base64.b64decode(data))

    tmp = tempfile.mkdtemp(prefix="fmo-panel-")
    lpath = os.path.join(tmp, "layout.json")
    ctx = fmodevtool.demo_ctx(lpath)
    ctx["live"] = [{"host": "203.0.113.7", "mapno": 102, "zone": 101, "x": 0.5, "y": 3.11,
                    "z": 9.4, "rot": 0.0}]
    port = free_port()
    fedevtool.start(port, "127.0.0.1", "tok",
                    lambda band=None, qs=None: fmodevtool.build_state(
                        ctx, band, want_plan=bool(qs and qs.get("plan"))),
                    lambda op: fmodevtool.apply_edit(ctx, op),
                    page=fmodevtool.PAGE, name="fmo-devtool-test")

    def layout():
        try:
            with open(lpath, encoding="utf-8") as fh:
                return json.load(fh)["bands"]
        except OSError:
            return {}

    def hq_rows():
        return layout().get("hq", {}).get("npcs", [])

    b = Browser()

    def mp(px, py):
        # fe_panel_browser.map_point speaks 256-px ART pixels; this canvas is
        # 1024 and there is no art, so scale
        b.pump(0.1)
        return b.map_point(px / 4.0, py / 4.0)
    try:
        print("\nthe FMO editor, in a real browser (HQ band, map 102)")
        b.goto("http://127.0.0.1:%d/?t=tok" % port, settle=3.0)
        shot(b, "01-loaded", "#mapcard")
        check("the page loads with NO JavaScript errors", not b.errors, "; ".join(b.errors))
        check("the floor plan arrived and was fitted",
              b.js("!!PLAN && PLAN.mapno === 102 && PLAN.boxes.length > 1000 && FITTED === 102"),
              repr(b.js("[!!PLAN, PLAN && PLAN.mapno, FITTED]")))
        check("the band tabs show the four lobbies and five places with counts",
              b.js("document.querySelectorAll('#bandtabs button').length") == 9
              and "9" in (b.js("document.querySelector('#bandtabs button.on').textContent") or ""),
              b.js("document.querySelector('#bandtabs button.on').textContent"))
        check("the served table lists prod's nine",
              b.js("document.querySelectorAll('#servedbox tr').length") == 9)
        check("the placer is hidden until a floor click",
              b.js("getComputedStyle(document.querySelector('#placer')).display") == "none")
        check("the live player (zone 101 = HQ) is reported as in THIS band",
              "in THIS band" in (b.js("document.querySelector('#livebar').textContent") or ""),
              b.js("document.querySelector('#livebar').textContent"))
        spill = b.js("(()=>{const c=document.querySelector('#mapcard').getBoundingClientRect();"
                     "return [...document.querySelectorAll('#mapcard *')]"
                     ".filter(e=>e.getBoundingClientRect().right>c.right+1)"
                     ".map(e=>e.tagName+'#'+e.id).slice(0,5)})()")
        check("nothing in the map card spills past its right edge", not spill, repr(spill))

        # ---- place on the floor: the row lands in the FILE with a box height
        # Kwangsu Son's spot (3.29, 8.59) is proven floor; go 3 m east of it.
        px, py = b.js("w2p(6.3, 8.6)")
        b.click(*mp(px, py))
        b.pump(0.4)
        check("clicking the floor opens the placer with the floor height",
              b.js("!document.querySelector('#placer').hidden")
              and "floor 3.1" in (b.js("document.querySelector('#placeat').textContent") or ""),
              b.js("document.querySelector('#placeat').textContent"))
        # tag_battle_ranking is a coliseum role, so it is not in the HQ menu:
        # the free key field is the path for any key the menu lacks
        b.js("document.querySelector('#placekey').value='0x82080940'")
        b.js("document.querySelector('#placecat').value='108'")
        b.js("document.querySelector('#placeface').value='90'")
        b.js("document.querySelector('#placelabel').value='Ranking.Board'")
        shot(b, "02-placer", "#mapcard")
        b.click_el("#placego")
        b.pump(0.8)
        rows = hq_rows()
        check("`place` writes ONE row to the layout file",
              len(rows) == 1 and rows[0]["key"] == 0x82080940 and rows[0]["cat"] == 108
              and rows[0]["label"] == "Ranking.Board" and rows[0]["face"] == 90.0, repr(rows))
        check("...with the floor height read off a box (3.0..3.2)",
              rows and 3.0 <= rows[0]["y"] <= 3.2, repr(rows))
        check("...at the clicked spot", rows and abs(rows[0]["x"] - 6.3) < 0.2
              and abs(rows[0]["z"] - 8.6) < 0.2, repr(rows))
        b.pump(1.6)
        check("the list now shows it and the band tab says it is in the layout",
              b.js("document.querySelectorAll('#npclist button').length") == 1
              and "✎" in (b.js("document.querySelector('#bandtabs button.on').textContent") or ""))
        shot(b, "03-placed", "#mapcard")

        # ---- select it, then DRAG it onto the console platform (3.50)
        px, py = b.js("w2p(6.3, 8.6)")
        b.click(*mp(px, py))
        b.pump(0.3)
        check("clicking the NPC selects it", b.js("SEL") == 0, repr(b.js("SEL")))
        check("...and opens its detail", b.js("!document.querySelector('#npcdetail').hidden"))
        tx, ty = b.js("w2p(-0.5, 2.0)")            # on the west platform
        b.drag(*mp(px, py), *mp(tx, ty))
        b.pump(0.8)
        rows = hq_rows()
        check("dragging it MOVES the row and re-snaps the height to the 3.50 platform",
              rows and abs(rows[0]["x"] + 0.5) < 0.2 and abs(rows[0]["z"] - 2.0) < 0.2
              and rows[0]["y"] == 3.5, repr(rows))
        b.pump(1.6)
        shot(b, "04-moved", "#mapcard")

        # ---- drag the orange handle: the facing changes
        hx, hy = b.js("(()=>{const n=selNpc();const [x,y]=w2p(n.x,n.z);"
                      "const a=n.face*Math.PI/180;return [x+Math.sin(a)*ROT_R, y-Math.cos(a)*ROT_R]})()")
        nx, ny = b.js("(()=>{const n=selNpc();return w2p(n.x,n.z)})()")
        b.drag(*mp(hx, hy), *mp(nx, ny + 40))   # straight south
        b.pump(0.8)
        rows = hq_rows()
        check("dragging the handle TURNS it (south = 180)",
              rows and rows[0]["face"] == 180.0, repr(rows))

        # ---- the void is refused by the server and nothing changes
        before = hq_rows()
        b.drag(*mp(*b.js("(()=>{const n=selNpc();return w2p(n.x,n.z)})()")),
               *mp(*b.js("w2p(120, 120)")))
        b.pump(0.8)
        msg = b.js("document.querySelector('#editmsg').textContent") or ""
        check("a drag into the void lands on the map's known floor and SAYS it is a guess",
              "GUESSED" in msg and hq_rows()[0]["y"] == 3.1 and hq_rows() != before, msg)

        # ---- a press held across a re-render still lands as a click
        x, y = b.center('#npclist button[data-idx="0"]')
        b.mouse("mouseMoved", x, y, buttons=0)
        b.mouse("mousePressed", x, y)
        b.pump(1.8)
        b.mouse("mouseReleased", x, y)
        b.pump(0.3)
        check("a press held across a re-render still toggles the selection",
              b.js("SEL") is None, repr(b.js("SEL")))

        # ---- import the served roster into the occupation band
        b.click_el('#bandtabs button[data-band="occ"]')
        b.pump(2.5)
        check("switching band re-fetches the plan and re-fits",
              b.js("ST.band") == "occ" and b.js("FITTED") == 102)
        check("...and the live bar says the player is in the HQ tab, not this one",
              "HQ lobby tab" in (b.js("document.querySelector('#livebar').textContent") or ""),
              b.js("document.querySelector('#livebar').textContent"))
        b.click_el("#importgo")
        b.pump(0.8)
        occ = layout().get("occ", {}).get("npcs", [])
        check("import copies the nine served rows into the layout",
              len(occ) == 9 and any(r["label"] == "Kwangsu.Son" and r["y"] == 3.11 for r in occ),
              repr([(r["label"], r["y"]) for r in occ]))
        b.pump(1.6)
        check("...and the env line is rendered back from them",
              "0x82081010@3.29,3.11,8.59#100=Kwangsu.Son" in (b.js("document.querySelector('#spec').value") or ""))
        shot(b, "05-imported-occ", "#mapcard")

        # ---- the three overlays: SE's marks, walked ground, prop labels
        b.click_el('#bandtabs button[data-band="hq"]')
        b.pump(2.5)
        check("the overlay bundle rode the plan: SE's marks and walked cells",
              b.js("MARKS.length") > 20 and b.js("WALK.cells.length") >= 2,
              repr(b.js("[MARKS.length, WALK.cells.length]")))
        for cid in ("shWalk", "shMarks", "shLabels"):
            b.click_el("#" + cid)
            b.pump(0.2)
        check("each overlay toggles without a JavaScript error", not b.errors, "; ".join(b.errors))
        for cid in ("shWalk", "shMarks"):
            b.click_el("#" + cid)             # back on
        b.pump(0.2)
        mx, my = b.js("(()=>{const m=MARKS.find(m=>!m.created&&m.face!=null);return w2p(m.x,m.z)})()")
        x, y = mp(mx, my)
        b.mouse("mouseMoved", x, y, buttons=0)
        b.pump(0.3)
        check("hovering a hollow diamond names it as SE's server slot",
              "server slot" in (b.js("document.querySelector('#mapread').textContent") or ""),
              b.js("document.querySelector('#mapread').textContent"))
        # a click on walked-only ground (the void cell at 150, 150) is accepted
        b.js("VIEW.cx=150; VIEW.cz=150; VIEW.s=40; drawMap()")
        px, py = b.js("w2p(150.2, 150.3)")
        b.click(*mp(px, py))
        b.pump(0.4)
        check("the placer reads the walked floor where no box exists",
              "walked ground" in (b.js("document.querySelector('#placeat').textContent") or ""),
              b.js("document.querySelector('#placeat').textContent"))
        b.js("document.querySelector('#placekey').value='0x82080300'")
        b.click_el("#placego")
        b.pump(0.8)
        rows = hq_rows()
        check("...and `place` accepts it with the walked mean height",
              any(r["key"] == 0x82080300 and r["y"] == 3.0 for r in rows), repr(rows))
        b.js("fit(); drawMap()")
        shot(b, "07-overlays", "#mapcard")

        # ---- the coliseum band: map 161, a different plan
        b.click_el('#bandtabs button[data-band="col"]')
        b.pump(2.5)
        check("the coliseum band draws map 161's plan",
              b.js("PLAN && PLAN.mapno") == 161 and b.js("ST.mapno") == 161)
        shot(b, "06-coliseum", "#mapcard")
        check("still no JavaScript errors", not b.errors, "; ".join(b.errors))
    finally:
        b.close()
    print()
    if fails:
        print("FAILED:")
        for f in fails:
            print("  " + f)
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
