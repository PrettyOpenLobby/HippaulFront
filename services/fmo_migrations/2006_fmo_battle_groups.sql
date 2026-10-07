-- Battle groups (services/fmoserver/battlegroups.py): every standing group's
-- members, leader, create-form numbers, comment and sortie settings, one JSON
-- document read and written whole, like fmo_coliseum. A restart restores the
-- groups from it and their members are attached again on their next login.
-- Battles in progress, group sorties and the board's on-sortie offer are not
-- here: a restart ends those with the battle connections.
CREATE TABLE fmo_battle_groups (
    id          SMALLINT PRIMARY KEY CHECK (id = 1),
    data        TEXT NOT NULL,
    updated_at  DOUBLE PRECISION NOT NULL
);
