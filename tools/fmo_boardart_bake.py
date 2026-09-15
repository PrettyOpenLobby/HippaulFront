#!/usr/bin/env python3
"""fmo_boardart_bake.py -- bake the art for the FRONT MISSION ONLINE City
Control board (services/boardfmo.py), from the client's own data where the
client has it.

    python tools/fmo_boardart_bake.py --client <install>                  # the backdrop only
    python tools/fmo_boardart_bake.py --client <install> --mplus DIR --oxanium TTF   # + the fonts

Writes services/boardart/fmo/ (NOT services/fedata/ or a services/fmo*.py
name: the deployment's git-sync restarts a service for anything under its
own tree):

  backdrop.png   FZ-10 Freedom City's SATELLITE IMAGE, the picture SE's war map
                 draws its sector grid over (the 2006 press war-map shot shows
                 the same picture). One 512x512 8-bit TIM2 at +0x10C0 inside
                 resource 93545 + MapKind (509 -> AJ/F40/D54.DAT), decoded by
                 tools/fmodatagen/fmotim2.py. This file is NOT tracked: it is
                 client data, and every checkout bakes its own.
  fmo-text[-bold].woff2 / .ttf
                 the page's TEXT face: M PLUS 1p Medium / Bold (SIL OFL 1.1),
                 "a regular dark font", ASCII + the few marks the page prints,
                 fixed-width digits for the columns. The .ttf copies are for
                 render.png (Pillow); only the .woff2 are served. These are
                 tracked, so the font step is optional.
  fmo-title.woff2 / .ttf
                 the TITLE face: Oxanium (SIL OFL 1.1) at weight 700, a squared
                 face in the spirit of the war map's HUD lettering. OURS, not
                 SE's: the client's HUD text is bitmap and there is no web font
                 of it.
  OFL-*.txt      the two licences.

The backdrop needs Pillow; the fonts need fontTools + brotli (a venv: system
Python has neither). tools/fmodata_build.py runs the backdrop step for you.

Sources: https://github.com/google/fonts/tree/main/ofl/mplus1p and
https://github.com/google/fonts/tree/main/ofl/oxanium
"""
import argparse
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, os.pardir, "services", "boardart", "fmo")
sys.path.insert(0, os.path.join(HERE, "fmodatagen"))

#: resource 93545 + MapKind holds the zone's satellite TIM2 at this offset
SAT_BASE, SAT_OFF, SAT_ZONE = 93545, 0x10C0, 509
#: printable ASCII + the marks the page prints (middle dot, en dash, arrows,
#: the multiplication sign of "x2")
UNICODES = list(range(0x20, 0x7F)) + [0x00B7, 0x2013, 0x2191, 0x2193, 0x00D7]


def resource_path(n):
    """The client's DAT for resource n: A{letter}/F{hundreds}/D{units}.DAT."""
    return "A%s/F%d/D%02d.DAT" % (chr(65 + n // 10000), (n // 100) % 100, n % 100)


def bake_backdrop(client, out=OUT):
    import fmofile
    import fmotim2
    rel = resource_path(SAT_BASE + SAT_ZONE)
    with open(fmofile.data_path(client, rel), "rb") as fh:
        blob = fh.read()
    im = fmotim2.decode(blob, SAT_OFF)
    if im is None or im.size != (512, 512):
        raise SystemExit("no 512x512 satellite TIM2 at %s+%#x" % (rel, SAT_OFF))
    # the TIM2 is already 8-bit indexed: keep it a 256-colour PNG
    im = im.convert("RGB").quantize(256)
    os.makedirs(out, exist_ok=True)
    im.save(os.path.join(out, "backdrop.png"), optimize=True)
    print("backdrop.png  <- %s +%#x (MapKind %d)" % (rel, SAT_OFF, SAT_ZONE))


def subset_font(src, out_base, weight=None):
    from fontTools import subset
    from fontTools.ttLib import TTFont
    f = TTFont(src)
    if weight is not None and "fvar" in f:
        from fontTools.varLib import instancer
        f = instancer.instantiateVariableFont(f, {"wght": weight})
    opts = subset.Options()
    opts.layout_features = ["kern", "liga", "tnum", "palt"]
    opts.name_IDs = ["*"]
    opts.name_languages = ["*"]
    opts.notdef_outline = True
    opts.hinting = False
    sub = subset.Subsetter(opts)
    sub.populate(unicodes=UNICODES)
    sub.subset(f)
    f.flavor = None
    f.save(out_base + ".ttf")
    f.flavor = "woff2"
    f.save(out_base + ".woff2")
    print("%s.woff2/.ttf  <- %s%s" % (os.path.basename(out_base), os.path.basename(src),
                                      "" if weight is None else " @ wght %d" % weight))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--client", "--data", dest="client", required=True,
                    help="the FRONT MISSION ONLINE install directory (or its Data directory)")
    ap.add_argument("--out", default=OUT, help="output directory (services/boardart/fmo)")
    ap.add_argument("--mplus",
                    help="directory holding mplus1p-medium.ttf, mplus1p-bold.ttf, OFL-mplus1p.txt")
    ap.add_argument("--oxanium",
                    help="Oxanium[wght].ttf (its OFL.txt beside it as OFL-Oxanium.txt)")
    a = ap.parse_args(argv)
    if bool(a.mplus) != bool(a.oxanium):
        ap.error("--mplus and --oxanium go together")
    os.makedirs(a.out, exist_ok=True)
    bake_backdrop(a.client, a.out)
    if a.mplus:
        subset_font(os.path.join(a.mplus, "mplus1p-medium.ttf"), os.path.join(a.out, "fmo-text"))
        subset_font(os.path.join(a.mplus, "mplus1p-bold.ttf"), os.path.join(a.out, "fmo-text-bold"))
        subset_font(a.oxanium, os.path.join(a.out, "fmo-title"), weight=700)
        shutil.copyfile(os.path.join(a.mplus, "OFL-mplus1p.txt"), os.path.join(a.out, "OFL-MPLUS1p.txt"))
        shutil.copyfile(os.path.join(os.path.dirname(a.oxanium), "OFL-Oxanium.txt"),
                        os.path.join(a.out, "OFL-Oxanium.txt"))
    print("baked into %s" % os.path.normpath(a.out))


if __name__ == "__main__":
    main()
