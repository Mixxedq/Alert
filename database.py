import sqlite3
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("DB_PATH", "data/bot.db")


class Database:
    def __init__(self):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_tables()
        logger.info(f"Database initialised at {DB_PATH}")

    def _init_tables(self):
        cur = self.conn.cursor()
        cur.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id     INTEGER PRIMARY KEY,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS tickers (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                symbol      TEXT NOT NULL,
                name        TEXT NOT NULL,
                added_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, symbol),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS user_settings (
                user_id             INTEGER PRIMARY KEY,
                price_threshold     REAL DEFAULT 3.0,
                volume_multiplier   REAL DEFAULT 3.0,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );
        """)
        self.conn.commit()

    # ── Users ─────────────────────────────────────────────────────────────────
    def add_user(self, user_id: int):
        try:
            self.conn.execute(
                "INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,)
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)", (user_id,)
            )
            self.conn.commit()
        except Exception as e:
            logger.error(f"add_user error: {e}")

    def get_all_users(self) -> list:
        try:
            cur = self.conn.execute("SELECT user_id FROM users")
            return [dict(row) for row in cur.fetchall()]
        except Exception as e:
            logger.error(f"get_all_users error: {e}")
            return []

    # ── Tickers ───────────────────────────────────────────────────────────────
    def add_ticker(self, user_id: int, symbol: str, name: str) -> bool:
        try:
            self.conn.execute(
                "INSERT OR IGNORE INTO tickers (user_id, symbol, name) VALUES (?, ?, ?)",
                (user_id, symbol, name)
            )
            self.conn.commit()
            return self.conn.execute(
                "SELECT changes()"
            ).fetchone()[0] > 0
        except Exception as e:
            logger.error(f"add_ticker error: {e}")
            return False

    def remove_ticker(self, user_id: int, symbol: str) -> bool:
        try:
            self.conn.execute(
                "DELETE FROM tickers WHERE user_id = ? AND symbol = ?",
                (user_id, symbol)
            )
            self.conn.commit()
            return self.conn.execute("SELECT changes()").fetchone()[0] > 0
        except Exception as e:
            logger.error(f"remove_ticker error: {e}")
            return False

    def get_user_tickers(self, user_id: int) -> list:
        try:
            cur = self.conn.execute(
                "SELECT symbol, name FROM tickers WHERE user_id = ? ORDER BY added_at",
                (user_id,)
            )
            return [dict(row) for row in cur.fetchall()]
        except Exception as e:
            logger.error(f"get_user_tickers error: {e}")
            return []

    # ── Settings ──────────────────────────────────────────────────────────────
    def get_user_settings(self, user_id: int) -> dict:
        try:
            cur = self.conn.execute(
                "SELECT price_threshold, volume_multiplier FROM user_settings WHERE user_id = ?",
                (user_id,)
            )
            row = cur.fetchone()
            if row:
                return dict(row)
            return {'price_threshold': 3.0, 'volume_multiplier': 3.0}
        except Exception as e:
            logger.error(f"get_user_settings error: {e}")
            return {'price_threshold': 3.0, 'volume_multiplier': 3.0}

    def save_user_settings(self, user_id: int, cfg: dict):
        try:
            self.conn.execute(
                """INSERT INTO user_settings (user_id, price_threshold, volume_multiplier)
                   VALUES (?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                       price_threshold   = excluded.price_threshold,
                       volume_multiplier = excluded.volume_multiplier""",
                (user_id, cfg['price_threshold'], cfg['volume_multiplier'])
            )
            self.conn.commit()
        except Exception as e:
            logger.error(f"save_user_settings error: {e}")
