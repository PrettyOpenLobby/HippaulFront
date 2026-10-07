-- The Coliseum (services/fmoserver/coliseum.py): the player-hosted arenas,
-- the game day's hosting count and each arena's win-streak records, one
-- JSON document read and written whole, like fmo_war. Registrations are not
-- here: they live in memory with the clients' waiting windows.
CREATE TABLE fmo_coliseum (
    id          SMALLINT PRIMARY KEY CHECK (id = 1),
    data        TEXT NOT NULL,
    updated_at  DOUBLE PRECISION NOT NULL
);
