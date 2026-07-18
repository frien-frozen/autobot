from __future__ import annotations

import time
from pathlib import Path

import aiosqlite


class Store:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS business_connections (
                connection_id TEXT PRIMARY KEY,
                owner_user_id INTEGER NOT NULL,
                is_enabled INTEGER NOT NULL DEFAULT 1,
                can_reply INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS contacts (
                chat_id INTEGER PRIMARY KEY,
                user_id INTEGER,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                bio TEXT,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_messages_chat_created
                ON messages(chat_id, created_at);

            CREATE TABLE IF NOT EXISTS chat_pauses (
                chat_id INTEGER PRIMARY KEY,
                pause_until REAL NOT NULL
            );
            """
        )
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Store is not connected")
        return self._db

    async def upsert_connection(
        self,
        connection_id: str,
        owner_user_id: int,
        is_enabled: bool,
        can_reply: bool,
    ) -> None:
        await self.db.execute(
            """
            INSERT INTO business_connections
                (connection_id, owner_user_id, is_enabled, can_reply, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(connection_id) DO UPDATE SET
                owner_user_id=excluded.owner_user_id,
                is_enabled=excluded.is_enabled,
                can_reply=excluded.can_reply,
                updated_at=excluded.updated_at
            """,
            (
                connection_id,
                owner_user_id,
                int(is_enabled),
                int(can_reply),
                time.time(),
            ),
        )
        await self.db.commit()

    async def can_reply(self, connection_id: str) -> bool:
        cursor = await self.db.execute(
            """
            SELECT can_reply, is_enabled
            FROM business_connections
            WHERE connection_id = ?
            """,
            (connection_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            # Connection update may arrive after first message; allow reply attempt.
            return True
        return bool(row["is_enabled"] and row["can_reply"])

    async def upsert_contact(
        self,
        chat_id: int,
        user_id: int | None,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
        bio: str | None,
    ) -> None:
        await self.db.execute(
            """
            INSERT INTO contacts
                (chat_id, user_id, username, first_name, last_name, bio, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                user_id=COALESCE(excluded.user_id, contacts.user_id),
                username=COALESCE(excluded.username, contacts.username),
                first_name=COALESCE(excluded.first_name, contacts.first_name),
                last_name=COALESCE(excluded.last_name, contacts.last_name),
                bio=COALESCE(excluded.bio, contacts.bio),
                updated_at=excluded.updated_at
            """,
            (
                chat_id,
                user_id,
                username,
                first_name,
                last_name,
                bio,
                time.time(),
            ),
        )
        await self.db.commit()

    async def get_contact(self, chat_id: int) -> dict | None:
        cursor = await self.db.execute(
            "SELECT * FROM contacts WHERE chat_id = ?",
            (chat_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def add_message(self, chat_id: int, role: str, text: str) -> None:
        await self.db.execute(
            """
            INSERT INTO messages (chat_id, role, text, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (chat_id, role, text, time.time()),
        )
        await self.db.commit()

    async def recent_messages(self, chat_id: int, limit: int) -> list[dict]:
        cursor = await self.db.execute(
            """
            SELECT role, text, created_at
            FROM messages
            WHERE chat_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (chat_id, limit),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in reversed(rows)]

    async def pause_chat(self, chat_id: int, minutes: int) -> None:
        await self.db.execute(
            """
            INSERT INTO chat_pauses (chat_id, pause_until)
            VALUES (?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET pause_until=excluded.pause_until
            """,
            (chat_id, time.time() + minutes * 60),
        )
        await self.db.commit()

    async def is_paused(self, chat_id: int) -> bool:
        cursor = await self.db.execute(
            "SELECT pause_until FROM chat_pauses WHERE chat_id = ?",
            (chat_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return False
        if row["pause_until"] <= time.time():
            await self.db.execute(
                "DELETE FROM chat_pauses WHERE chat_id = ?",
                (chat_id,),
            )
            await self.db.commit()
            return False
        return True
