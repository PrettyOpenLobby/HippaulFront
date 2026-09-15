#!/usr/bin/env python3
"""fmofile.py -- FMO's resource index -> data-file path, and what that says
about which MapNo / MapKind values actually exist.

    python fmofile.py path 93645              # one index
    python fmofile.py scan --client <install> # which map/script ids are present

THE MAPPING, read off 0x6113DAE0 (the resolver `FUN_6113EAA0` calls, and whose
returning 0 is exactly how a resource ends up with size 0):

    q, r      = divmod(index, 100)
    letter1   = 'A' + q // 1000
    letter2   = 'A' + (q % 1000) // 100
    dir2      = (q % 100)
    path      = data\\<letter1><letter2>\\F<dir2:02d>\\D<r:02d>.DAT

with one special case at 0x6113DB29: index 0x16D5C (93532) is
`\\version\\verwinjp.cfg` instead.

AND THE INDEX ITSELF, from 0x611253C0 and the table at 0x61337AC0:

    index = BASE[type] + id       BASE = [56117, 53557, 55605, 93545, 95593]

    type 2 = the MAP,    id = MapNo    -> 55605 + MapNo,   512 slots
    type 3 = the SCRIPT, id = MapKind  -> 93545 + MapKind, 2048 slots

WARNING: THE ID IS PART OF THE INDEX. An earlier reading had `index = BASE[type]
+ a4` using the caller's 4th argument, which is a CONSTANT - that predicted the
script resource could not vary with MapKind, and it plainly did (459520 bytes
at MapKind=100, 271584 at 99). The stack depth is what settles it: at
`0x611254BF` five pushes are live, so `[esp+0x1C]` is argument TWO, the id.
Recount the pushes before trusting any argument mapping in this function.
"""
import os
import sys

BASE = [56117, 53557, 55605, 93545, 95593]      # table at 0x61337AC0
TYPE_MAP, TYPE_SCRIPT = 2, 3
VERSION_CFG_INDEX = 0x16D5C


def data_dir(client):
    """The Data directory of an install. `client` may be the install directory
    (the one holding PolBoot.exe and Data/) or the Data directory itself; the
    first candidate that holds the AI/ resource tree wins."""
    for cand in (os.path.join(client, "Data"), os.path.join(client, "data"), client):
        if os.path.isdir(os.path.join(cand, "AI")):
            return cand
    raise SystemExit("no Data/AI directory under %s - point --client at the "
                     "FRONT MISSION ONLINE install directory" % client)


def data_path(client, rel):
    """The absolute path of a resource given relative to Data/ ('AI/F32/D15.DAT')."""
    return os.path.join(data_dir(client), *rel.replace("\\", "/").split("/"))


def index_of(rtype, rid):
    return BASE[rtype] + rid


def path_of(index):
    """The relative path 0x6113DAE0 builds, with backslashes as it writes them."""
    if index == VERSION_CFG_INDEX:
        return r"version\verwinjp.cfg"
    q, r = divmod(index, 100)
    l1 = chr(ord("A") + q // 1000)
    l2 = chr(ord("A") + (q % 1000) // 100)
    return "data\\%s%s\\F%02d\\D%02d.DAT" % (l1, l2, q % 100, r)


def rel_of(index):
    """path_of() without the leading data\\ and with forward slashes: the form
    the other readers take ('AI/F08/D39.DAT')."""
    return path_of(index).replace("\\", "/").split("/", 1)[1]


def exists(root, index, suffixes=("", ".slc")):
    """Is that file present under `root` (the install directory)? `.slc`
    because a patch mirror keeps every blob under that extension. The client
    spells the directory `data`; installs spell it `Data`, so both are tried
    for the sake of case-sensitive file systems."""
    rel = path_of(index).replace("\\", os.sep)
    for s in suffixes:
        for r in (rel, "Data" + rel[4:]):
            p = os.path.join(root, r + s)
            if os.path.exists(p):
                return p
    return None


def scan(root):
    print("root: %s" % root)
    for rtype, name, count in ((TYPE_MAP, "MAP  (MapNo)", 512),
                               (TYPE_SCRIPT, "SCRIPT (MapKind)", 2048)):
        present = []
        for rid in range(count):
            if exists(root, index_of(rtype, rid)):
                present.append(rid)
        print("\ntype %d  %-18s base %d   %d of %d present"
              % (rtype, name, BASE[rtype], len(present), count))
        if present:
            print("   ids: %s" % _ranges(present))
        else:
            print("   ids: NONE FOUND - either the root is wrong or this tree "
                  "does not carry them")


def _ranges(xs):
    out, start, prev = [], xs[0], xs[0]
    for x in xs[1:]:
        if x == prev + 1:
            prev = x
            continue
        out.append((start, prev))
        start = prev = x
    out.append((start, prev))
    return ", ".join(str(a) if a == b else "%d-%d" % (a, b) for a, b in out)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    if sys.argv[1] == "path":
        for a in sys.argv[2:]:
            i = int(a, 0)
            print("  %-8d -> %s" % (i, path_of(i)))
    elif sys.argv[1] == "scan":
        argv = sys.argv[2:]
        if "--client" in argv:
            argv.pop(argv.index("--client"))
        if not argv:
            print("scan needs --client <install>")
            return 1
        scan(argv[0])
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
