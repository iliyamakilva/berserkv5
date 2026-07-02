import json
from aiogram.dispatcher.storage import BaseStorage
import db


def _ensure_table():
    db.cur.execute(
        """
        CREATE TABLE IF NOT EXISTS fsm_state(
            chat_id TEXT,
            user_id TEXT,
            state TEXT,
            data TEXT DEFAULT '{}',
            bucket TEXT DEFAULT '{}',
            PRIMARY KEY (chat_id, user_id)
        )
        """
    )
    db.conn.commit()


class SQLiteStorage(BaseStorage):
    def __init__(self):
        _ensure_table()

    async def close(self):
        pass

    async def wait_closed(self):
        pass

    def _row(self, chat_id, user_id):
        db.cur.execute(
            "SELECT * FROM fsm_state WHERE chat_id=? AND user_id=?",
            (chat_id, user_id),
        )
        return db.cur.fetchone()

    def _upsert(self, chat_id, user_id, **fields):
        row = self._row(chat_id, user_id)
        if row is None:
            db.cur.execute(
                """
                INSERT INTO fsm_state(chat_id, user_id, state, data, bucket)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    user_id,
                    fields.get("state"),
                    fields.get("data", "{}"),
                    fields.get("bucket", "{}"),
                ),
            )
        else:
            sets, values = [], []
            for key in ("state", "data", "bucket"):
                if key in fields:
                    sets.append(f"{key}=?")
                    values.append(fields[key])
            if sets:
                values.extend([chat_id, user_id])
                db.cur.execute(
                    f"UPDATE fsm_state SET {', '.join(sets)} WHERE chat_id=? AND user_id=?",
                    values,
                )
        db.conn.commit()

    def _cleanup(self, chat_id, user_id):
        row = self._row(chat_id, user_id)
        if row and not row["state"] and row["data"] == "{}" and row["bucket"] == "{}":
            db.cur.execute(
                "DELETE FROM fsm_state WHERE chat_id=? AND user_id=?",
                (chat_id, user_id),
            )
            db.conn.commit()

    async def get_state(self, *, chat=None, user=None, default=None):
        chat_id, user_id = map(str, self.check_address(chat=chat, user=user))
        row = self._row(chat_id, user_id)
        if row is None or row["state"] is None:
            return self.resolve_state(default)
        return row["state"]

    async def get_data(self, *, chat=None, user=None, default=None):
        chat_id, user_id = map(str, self.check_address(chat=chat, user=user))
        row = self._row(chat_id, user_id)
        if row is None:
            return {}
        return json.loads(row["data"])

    async def update_data(self, *, chat=None, user=None, data=None, **kwargs):
        chat_id, user_id = map(str, self.check_address(chat=chat, user=user))
        current = await self.get_data(chat=chat_id, user=user_id)
        current.update(data or {}, **kwargs)
        self._upsert(chat_id, user_id, data=json.dumps(current))

    async def set_state(self, *, chat=None, user=None, state=None):
        chat_id, user_id = map(str, self.check_address(chat=chat, user=user))
        self._upsert(chat_id, user_id, state=self.resolve_state(state))

    async def set_data(self, *, chat=None, user=None, data=None):
        chat_id, user_id = map(str, self.check_address(chat=chat, user=user))
        self._upsert(chat_id, user_id, data=json.dumps(data or {}))
        self._cleanup(chat_id, user_id)

    async def reset_state(self, *, chat=None, user=None, with_data=True):
        chat_id, user_id = map(str, self.check_address(chat=chat, user=user))
        await self.set_state(chat=chat_id, user=user_id, state=None)
        if with_data:
            await self.set_data(chat=chat_id, user=user_id, data={})
        self._cleanup(chat_id, user_id)

    def has_bucket(self):
        return True

    async def get_bucket(self, *, chat=None, user=None, default=None):
        chat_id, user_id = map(str, self.check_address(chat=chat, user=user))
        row = self._row(chat_id, user_id)
        if row is None:
            return {}
        return json.loads(row["bucket"])

    async def set_bucket(self, *, chat=None, user=None, bucket=None):
        chat_id, user_id = map(str, self.check_address(chat=chat, user=user))
        self._upsert(chat_id, user_id, bucket=json.dumps(bucket or {}))
        self._cleanup(chat_id, user_id)

    async def update_bucket(self, *, chat=None, user=None, bucket=None, **kwargs):
        chat_id, user_id = map(str, self.check_address(chat=chat, user=user))
        current = await self.get_bucket(chat=chat_id, user=user_id)
        current.update(bucket or {}, **kwargs)
        self._upsert(chat_id, user_id, bucket=json.dumps(current))
