#!/usr/bin/env python3
"""Run every Front Mission Online selftest, and exit non-zero if any fails.

    python tools/fmo_run_all.py              # everything
    python tools/fmo_run_all.py -k store     # only suites whose name contains
    python tools/fmo_run_all.py -v           # stream each suite's own output

The list is explicit, not globbed: a suite that is not registered here does
not exist. No client is needed. The suites import OpenLobby's polcore, so the
core has to be checked out beside this repository (or named by OPENLOBBY_DIR,
or OPENLOBBY_SERVICES for its services/). The ones that touch the database
each get a new, empty one from the core's tools/pgtest.py, which starts a
throwaway PostgreSQL in Docker or uses the server POL_TEST_DATABASE_URL names;
without either they SKIP, and POL_TEST_REQUIRE_DB=1 makes that a failure. The
world channel suite needs the generated cipher tables
(tools/gen_blowfish_tables.py) and says so when they are missing.

POL_DATA_DIR, POL_RESOURCE_DIR, POL_LOG_DIR and POL_LOGIN_PW_KEYFILE that
are not set point into a temporary directory made for the run and removed
at the end (scratch_state).
"""
import argparse
import atexit
import os
import shutil
import subprocess
import sys
import tempfile
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
    # fmo.db and the board's Discord state files, imported into PostgreSQL
    ("fmo_import", [PY, os.path.join(HERE, "fmo_import_test.py")], HERE),
    # the lobby NPC layout file the editor writes
    ("fmolayout",  [PY, "fmolayout.py", "--selftest"],  SERVICES),
    # the City Control board and its place in the boards host
    ("fmo_board",  [PY, "fmo_board_test.py"],           HERE),
    # the title plugin: the Viewer's profile out of the pilot database
    ("fmo_title",  [PY, "fmo_title_test.py"],           HERE),
    # every patch a test makes through fmo.py reaches the fmoserver module
    # that owns the name
    ("facade",     [PY, "facade_rebind_check.py"],      HERE),
]


#: The state paths a suite falls back to when they are unset (see scratch_state).
SCRATCH_VARS = ("POL_DATA_DIR", "POL_RESOURCE_DIR", "POL_LOG_DIR",
                "POL_LOGIN_PW_KEYFILE")


def scratch_state():
    """Point every state path a suite may fall back to at a directory made
    for this run and removed when it ends, unless the caller set it.

    A suite that finds no POL_DATA_DIR uses /data, which on Windows is the
    root of the current drive, so a run could read and write a real server's
    files there. A value already set wins; POL_RESOURCE_DIR then follows
    POL_DATA_DIR, as the services derive it. Returns the directory made, or
    None when every variable was set.
    """
    missing = [k for k in SCRATCH_VARS if not os.environ.get(k, "").strip()]
    if not missing:
        return None
    root = tempfile.mkdtemp(prefix="fmo-run-")
    atexit.register(shutil.rmtree, root, True)
    if "POL_DATA_DIR" in missing:
        os.environ["POL_DATA_DIR"] = os.path.join(root, "data")
        os.makedirs(os.environ["POL_DATA_DIR"])
    if "POL_RESOURCE_DIR" in missing:
        os.environ["POL_RESOURCE_DIR"] = os.path.join(os.environ["POL_DATA_DIR"],
                                                      "resources")
        if os.environ["POL_RESOURCE_DIR"].startswith(root):
            os.makedirs(os.environ["POL_RESOURCE_DIR"], exist_ok=True)
    if "POL_LOG_DIR" in missing:
        os.environ["POL_LOG_DIR"] = os.path.join(root, "logs")
        os.makedirs(os.environ["POL_LOG_DIR"])
    if "POL_LOGIN_PW_KEYFILE" in missing:
        os.makedirs(os.path.join(root, "keys"))
        os.environ["POL_LOGIN_PW_KEYFILE"] = os.path.join(root, "keys", "login-pw.key")
    return root


def main():
    scratch_state()
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
