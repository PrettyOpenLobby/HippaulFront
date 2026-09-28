# One image for the Front Mission Online service and its optional board;
# the entrypoint is chosen per service in docker-compose.yml.
#
# Built on the OpenLobby core image, which carries polcore (the PostgreSQL
# and Valkey layer every service shares) and its drivers. Build the core
# first, or point OPENLOBBY_IMAGE at the image you use.
ARG OPENLOBBY_IMAGE=openlobby:latest
FROM ${OPENLOBBY_IMAGE}

WORKDIR /app
COPY . /app/

# Refuse to build an image without the generated cipher tables (see README:
# run tools/gen_blowfish_tables.py before building). The game data under
# fmodata/ is optional: the server runs without it, with the gaps the README
# lists, until tools/fmodata_build.py has been run against your client.
RUN python - <<'EOF'
import os, sys
missing = [p for p in ("fmo_blowfish_P.bin", "fmo_blowfish_S.bin") if not os.path.exists(p)]
if missing:
    sys.exit("missing generated tables: %s - run tools/gen_blowfish_tables.py first" % ", ".join(missing))
if not os.path.isdir("polcore"):
    sys.exit("the base image has no polcore/ - build it FROM the OpenLobby image")
if not os.path.exists("fmodata/fmo-ranks.tsv"):
    print("NOTE: fmodata/ holds only the authored events table; run "
          "tools/fmodata_build.py against your client install for the rank "
          "ladder, the class curve and the catalogues", file=sys.stderr)
EOF

ENTRYPOINT ["python", "fmo.py"]
