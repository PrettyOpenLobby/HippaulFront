#!/usr/bin/env python3
"""Run every Front Mission Online selftest, and exit non-zero if any fails.

    python tools/fmo_run_all.py              # everything
    python tools/fmo_run_all.py -k store     # only suites whose name contains
    python tools/fmo_run_all.py -v           # stream each suite's own output

The list is explicit, not globbed: a suite that is not registered here does
not exist. No Docker, no client and no core checkout is needed; the world
channel suite needs the generated cipher tables (tools/gen_blowfish_tables.py)
and says so when they are missing.
"""
import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SERVICES = os.path.normpath(os.path.join(HERE, os.pardir, "services"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")
PY = sys.executable

#: (name, argv, cwd)
SUITES = [
    # the world responder: every request the client sends, driven through the
    # real dispatcher, and the pushes it can emit
    ("fmo",        [PY, "fmo.py", "--selftest"],        SERVICES),
    # the UDP world channel: the cipher, the key derivation, the framing
    ("fmoworld",   [PY, "fmoworld.py", "--selftest"],   SERVICES),
    # the second (community) server codec and its records
    ("fmomsn",     [PY, "fmomsn.py", "--selftest"],     SERVICES),
    # the war: the sector query, the phase clock, the control model
    ("fmowar",     [PY, "fmowar.py", "--selftest"],     SERVICES),
    # the player database in isolation, then driven through a session
    ("fmostore",   [PY, "fmostore.py", "--selftest"],   SERVICES),
    # (under the code defaults, as fmo.py --selftest runs: FMO_RELEASE_DEFAULTS=0)
    ("fmo_store",  [PY, os.path.join(HERE, "fmo_store_test.py")], SERVICES),
    # the lobby NPC layout file the editor writes
    ("fmolayout",  [PY, "fmolayout.py", "--selftest"],  SERVICES),
    # the City Control board and its place in the boards host
    ("fmo_board",  [PY, "fmo_board_test.py"],           HERE),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", default="", help="only suites whose name contains this")
    ap.add_argument("-v", action="store_true", help="stream each suite's output")
    a = ap.parse_args()
    chosen = [s for s in SUITES if a.k in s[0]]
    failed = []
    for name, argv, cwd in chosen:
        t0 = time.time()
        env = dict(os.environ, FMO_RELEASE_DEFAULTS="0")
        r = subprocess.run(argv, cwd=cwd, capture_output=not a.v, text=True,
                           timeout=900, env=env)
        dt = time.time() - t0
        ok = r.returncode == 0
        print("%-12s %s  (%.1fs)" % (name, "ok" if ok else "FAIL", dt), flush=True)
        if not ok:
            failed.append(name)
            if not a.v:
                tail = (r.stdout or "").splitlines()[-25:] + (r.stderr or "").splitlines()[-25:]
                for line in tail:
                    print("    " + line)
    print("%d/%d suites passed" % (len(chosen) - len(failed), len(chosen)))
    if failed:
        print("failed: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
