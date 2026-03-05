from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "games" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "round_count" SMALLINT NOT NULL  DEFAULT 0,
    "created_at" TIMESTAMPTZ NOT NULL  DEFAULT CURRENT_TIMESTAMP,
    "winner_id" UUID REFERENCES "users" ("id") ON DELETE SET NULL
);
        CREATE TABLE IF NOT EXISTS "user_games" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "tolls_paid" BIGINT NOT NULL  DEFAULT 0,
    "tiles_purchased" SMALLINT NOT NULL  DEFAULT 0,
    "buildings_built" SMALLINT NOT NULL  DEFAULT 0,
    "placement" SMALLINT,
    "game_id" INT NOT NULL REFERENCES "games" ("id") ON DELETE CASCADE,
    "user_id" UUID NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE,
    CONSTRAINT "uid_user_games_user_id_06f037" UNIQUE ("user_id", "game_id")
);
CREATE INDEX IF NOT EXISTS "idx_user_games_game_id_88c866" ON "user_games" ("game_id");
CREATE INDEX IF NOT EXISTS "idx_user_games_user_id_ab1f9e" ON "user_games" ("user_id");"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "games";
        DROP TABLE IF EXISTS "user_games";"""
