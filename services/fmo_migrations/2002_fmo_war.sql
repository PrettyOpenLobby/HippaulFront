-- The war (services/fmowar.py) and the sector-win ledger
-- (services/fmoserver/sectorwins.py), formerly fmowar.json and
-- fmo_sector_wins.json on the data volume.

-- The war state is one JSON document ({"sectors", "phases", "log"}) that
-- fmowar.War reads whole and writes whole, as it did the file. One row.
-- updated_at (epoch seconds) is what the City Control board showed as the
-- file's mtime.
CREATE TABLE fmo_war (
    id          SMALLINT PRIMARY KEY CHECK (id = 1),
    data        TEXT NOT NULL,
    updated_at  DOUBLE PRECISION NOT NULL
);

-- One win by `nation` on (zone, tile) at `won_at` (unix seconds). The game
-- keeps two days of it (FMO_SECTOR_WINS_KEEP) so a sector mission in
-- progress survives a restart. Two wins in the same second are two rows.
CREATE TABLE fmo_sector_win (
    zone     BIGINT NOT NULL,
    tile     BIGINT NOT NULL,
    nation   BIGINT NOT NULL,
    won_at   BIGINT NOT NULL
);

CREATE INDEX fmo_sector_win_key ON fmo_sector_win (zone, tile, nation, won_at);
CREATE INDEX fmo_sector_win_at ON fmo_sector_win (won_at);
