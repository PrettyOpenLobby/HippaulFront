"""Decode FMO's TIM2 textures so a human can see whether the UI text is PIXELS.

The client turned out to hold barely any Japanese *text*, yet the HUD and menu
labels read Japanese on screen. The remaining explanation is that they are drawn
into textures, as a PS2 port normally does - and the only way to settle that is
to look. TIM2 images are embedded inside the Data DATs (~9,000 of them), not
shipped as loose files, so this finds them by magic.

    python fmotim2.py --client <install> --list AJ/F40/D54.DAT   # images inside one DAT
    python fmotim2.py --client <install> --png FILE OFF OUT       # decode one to PNG
    python fmotim2.py --client <install> --sheet OUT N            # N label-shaped images -> a contact sheet

FILE may be absolute, or relative to the install's Data directory. Needs Pillow.
"""
import argparse
import os
import struct
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fmofile                                                     # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def images_in(blob):
    """Yield (offset, width, height, image_type, clut_type, header) per TIM2."""
    pos = 0
    while True:
        i = blob.find(b"TIM2", pos)
        if i < 0:
            return
        pos = i + 4
        if i + 0x30 > len(blob):
            return
        w, h = struct.unpack_from("<HH", blob, i + 0x24)
        if not (8 <= w <= 2048 and 8 <= h <= 2048):
            continue
        yield i, w, h, blob[i + 0x23], blob[i + 0x22]


def decode(blob, off):
    """One TIM2 picture -> a PIL image. Handles the 4- and 8-bit indexed forms
    the UI uses; anything else is returned as None rather than guessed at."""
    tot, clut_sz, img_sz = struct.unpack_from("<III", blob, off + 0x10)
    hdr_sz, clut_colors = struct.unpack_from("<HH", blob, off + 0x1C)
    img_type, clut_type = blob[off + 0x23], blob[off + 0x22]
    w, h = struct.unpack_from("<HH", blob, off + 0x24)
    body = off + 0x10 + hdr_sz
    px = blob[body:body + img_sz]
    clut = blob[body + img_sz:body + img_sz + clut_sz]
    if img_type not in (4, 5) or not clut:
        return None
    # PS2 CLUTs are 32-bit RGBA with alpha at half scale, and 8-bit palettes are
    # stored in a swizzled block order that has to be undone before use.
    pal = []
    for i in range(0, min(len(clut), clut_colors * 4), 4):
        r, g, b, a = clut[i:i + 4]
        pal.append((r, g, b, min(255, a * 2)))
    if img_type == 5 and len(pal) >= 256:
        order = []
        for i in range(0, 256, 32):
            order += list(range(i, i + 8))
            order += list(range(i + 16, i + 24))
            order += list(range(i + 8, i + 16))
            order += list(range(i + 24, i + 32))
        pal = [pal[i] for i in order]
    idx = []
    if img_type == 4:
        for byte in px:
            idx.append(byte & 0xF)
            idx.append(byte >> 4)
    else:
        idx = list(px)
    if len(idx) < w * h:
        return None
    img = Image.new("RGBA", (w, h))
    img.putdata([pal[i] if i < len(pal) else (0, 0, 0, 0) for i in idx[:w * h]])
    return img


def _resolve(client, rel):
    if os.path.isabs(rel):
        return rel
    if not client:
        raise SystemExit("a relative FILE needs --client <install>")
    return fmofile.data_path(client, rel)


def list_images(client, rel):
    blob = open(_resolve(client, rel), "rb").read()
    for off, w, h, it, ct in images_in(blob):
        print(f"  {off:08x} {w:4d}x{h:<4d} imgtype {it} cluttype {ct}")


def to_png(client, rel, off, out):
    blob = open(_resolve(client, rel), "rb").read()
    img = decode(blob, int(off, 0))
    if img is None:
        print("unsupported image type")
        return
    img.save(out)
    print("wrote", out, img.size)


def sheet(client, out, want):
    """A contact sheet of the widest, shortest images - the shape a row of
    menu labels or a font strip takes."""
    data = fmofile.data_dir(client)
    found = []
    for root, _d, names in os.walk(data):
        for n in names:
            p = os.path.join(root, n)
            blob = open(p, "rb").read()
            for off, w, h, _it, _ct in images_in(blob):
                if w >= 256 and h <= 128 and w / h >= 4:
                    found.append((os.path.relpath(p, data), off, w, h, blob))
            if len(found) >= want:
                break
        if len(found) >= want:
            break
    cells = []
    for rel, off, w, h, blob in found[:want]:
        img = decode(blob, off)
        if img:
            cells.append((rel, off, img))
    if not cells:
        print("nothing decodable found")
        return
    W = max(c[2].width for c in cells)
    H = sum(c[2].height + 4 for c in cells)
    out_img = Image.new("RGBA", (W, H), (24, 24, 28, 255))
    y = 0
    for rel, off, img in cells:
        out_img.alpha_composite(img, (0, y))
        y += img.height + 4
        print(f"  {rel} @{off:08x} {img.size}")
    out_img.save(out)
    print("wrote", out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", help="the FRONT MISSION ONLINE install directory")
    ap.add_argument("--list")
    ap.add_argument("--png", nargs=3, metavar=("FILE", "OFF", "OUT"))
    ap.add_argument("--sheet", nargs=2, metavar=("OUT", "N"))
    a = ap.parse_args()
    if a.list:
        list_images(a.client, a.list)
    elif a.png:
        to_png(a.client, *a.png)
    elif a.sheet:
        if not a.client:
            ap.error("--sheet needs --client <install>")
        sheet(a.client, a.sheet[0], int(a.sheet[1]))
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
