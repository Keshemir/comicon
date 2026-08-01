"""SQLite: счётчики, кеш имён, журнал выдач.

Фотографий здесь нет и быть не должно — исходник гостя удаляется сразу после
отправки паспорта. В базе только то, без чего не работает лимит и статистика.
"""

from __future__ import annotations

import datetime as dt

import aiosqlite

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS issued (
    user_id     INTEGER NOT NULL,
    serial      TEXT    NOT NULL,
    totem       TEXT    NOT NULL,
    name_out    TEXT    NOT NULL,
    used_ai     INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS issued_user ON issued(user_id);
CREATE INDEX IF NOT EXISTS issued_day  ON issued(created_at);

-- Кеш тюркизации. На комикконе имена повторяются массово, и повтор не должен
-- стоить ни денег, ни пяти секунд ожидания в живой очереди.
CREATE TABLE IF NOT EXISTS name_cache (
    source  TEXT PRIMARY KEY,
    latin   TEXT NOT NULL,
    meaning TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


async def connect() -> aiosqlite.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(config.DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.executescript(SCHEMA)
    await db.commit()
    return db


async def issued_count(db: aiosqlite.Connection, user_id: int) -> int:
    async with db.execute("SELECT COUNT(*) FROM issued WHERE user_id = ?", (user_id,)) as cur:
        return (await cur.fetchone())[0]


async def record(db: aiosqlite.Connection, user_id: int, serial: str,
                 totem: str, name_out: str, used_ai: bool) -> None:
    """Пишем ТОЛЬКО после успешной выдачи.

    Лимит «один паспорт» считается по успехам: если Gemini лёг или лицо не
    нашлось, попытка не должна сгорать — иначе гость уходит ни с чем.
    """
    await db.execute(
        "INSERT INTO issued (user_id, serial, totem, name_out, used_ai, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, serial, totem, name_out, int(used_ai), dt.datetime.now().isoformat(timespec="seconds")),
    )
    await db.commit()


async def reset_user(db: aiosqlite.Connection, user_id: int) -> int:
    cur = await db.execute("DELETE FROM issued WHERE user_id = ?", (user_id,))
    await db.commit()
    return cur.rowcount


async def next_serial(db: aiosqlite.Connection) -> tuple[str, str]:
    async with db.execute("SELECT COUNT(*) FROM issued") as cur:
        n = (await cur.fetchone())[0] + 1
    return f"{122024 + n:09d}", f"{n:04d}"


async def cached_name(db: aiosqlite.Connection, source: str) -> tuple[str, str] | None:
    if not config.NAME_CACHE:
        return None
    async with db.execute(
        "SELECT latin, meaning FROM name_cache WHERE source = ?", (source.strip().lower(),)
    ) as cur:
        row = await cur.fetchone()
    return (row["latin"], row["meaning"]) if row else None


async def cache_name(db: aiosqlite.Connection, source: str, latin: str, meaning: str) -> None:
    await db.execute(
        "INSERT OR REPLACE INTO name_cache (source, latin, meaning) VALUES (?, ?, ?)",
        (source.strip().lower(), latin, meaning),
    )
    await db.commit()


async def ai_used_today(db: aiosqlite.Connection) -> int:
    today = dt.date.today().isoformat()
    async with db.execute(
        "SELECT COUNT(*) FROM issued WHERE used_ai = 1 AND created_at LIKE ?", (f"{today}%",)
    ) as cur:
        return (await cur.fetchone())[0]


async def stats(db: aiosqlite.Connection) -> dict:
    today = dt.date.today().isoformat()
    out: dict = {}
    async with db.execute("SELECT COUNT(*) FROM issued") as cur:
        out["total"] = (await cur.fetchone())[0]
    async with db.execute(
        "SELECT COUNT(*) FROM issued WHERE created_at LIKE ?", (f"{today}%",)
    ) as cur:
        out["today"] = (await cur.fetchone())[0]
    async with db.execute(
        "SELECT totem, COUNT(*) c FROM issued GROUP BY totem ORDER BY c DESC"
    ) as cur:
        out["by_totem"] = {r["totem"]: r["c"] for r in await cur.fetchall()}
    async with db.execute("SELECT COUNT(*) FROM name_cache") as cur:
        out["names_cached"] = (await cur.fetchone())[0]
    out["ai_today"] = await ai_used_today(db)
    return out


async def get_flag(db: aiosqlite.Connection, key: str, default: str) -> str:
    async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cur:
        row = await cur.fetchone()
    return row["value"] if row else default


async def set_flag(db: aiosqlite.Connection, key: str, value: str) -> None:
    await db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value)
    )
    await db.commit()
