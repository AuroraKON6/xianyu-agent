"""SQLite database manager for multi-account xianyu operations.

Adapted from xianyu-super-butler with simplified schema focused on:
- Multi-account cookie management
- Keyword-based reply rules
- AI reply settings and conversation history
- Order tracking
- Product info caching
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


class DBManager:
    """SQLite database manager with thread-safe operations."""

    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = os.getenv("DB_PATH", "data/xianyu_data.db")

        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        self.db_path = db_path
        self.conn = None
        self.lock = threading.RLock()
        self.init_db()

    def init_db(self):
        """Initialize database schema."""
        try:
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            cursor = self.conn.cursor()

            # Cookies table (multi-account)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS cookies (
                    id TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    remark TEXT DEFAULT '',
                    auto_confirm INTEGER DEFAULT 1,
                    pause_duration INTEGER DEFAULT 10,
                    show_browser INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Keywords table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS keywords (
                    cookie_id TEXT,
                    keyword TEXT,
                    reply TEXT,
                    item_id TEXT DEFAULT '',
                    type TEXT DEFAULT 'text',
                    image_url TEXT DEFAULT '',
                    priority INTEGER DEFAULT 0,
                    FOREIGN KEY (cookie_id) REFERENCES cookies(id) ON DELETE CASCADE
                )
            """)

            # Cookie status table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS cookie_status (
                    cookie_id TEXT PRIMARY KEY,
                    enabled BOOLEAN DEFAULT TRUE,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (cookie_id) REFERENCES cookies(id) ON DELETE CASCADE
                )
            """)

            # AI reply settings per account
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ai_reply_settings (
                    cookie_id TEXT PRIMARY KEY,
                    ai_enabled BOOLEAN DEFAULT FALSE,
                    model_name TEXT DEFAULT 'deepseek-v4-flash',
                    api_key TEXT DEFAULT '',
                    base_url TEXT DEFAULT 'https://api.deepseek.com',
                    max_discount_percent INTEGER DEFAULT 10,
                    max_discount_amount INTEGER DEFAULT 100,
                    max_bargain_rounds INTEGER DEFAULT 3,
                    custom_prompts TEXT DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (cookie_id) REFERENCES cookies(id) ON DELETE CASCADE
                )
            """)

            # AI conversation history
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ai_conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cookie_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    item_id TEXT DEFAULT '',
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    intent TEXT DEFAULT '',
                    bargain_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (cookie_id) REFERENCES cookies(id) ON DELETE CASCADE
                )
            """)

            # Item cache
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ai_item_cache (
                    item_id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    price REAL DEFAULT 0,
                    description TEXT DEFAULT '',
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Orders table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    cookie_id TEXT NOT NULL,
                    item_id TEXT DEFAULT '',
                    buyer_id TEXT DEFAULT '',
                    status TEXT DEFAULT 'processing',
                    amount REAL DEFAULT 0,
                    delivery_payload TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (cookie_id) REFERENCES cookies(id) ON DELETE CASCADE
                )
            """)

            # Items table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS items (
                    item_id TEXT PRIMARY KEY,
                    cookie_id TEXT NOT NULL,
                    title TEXT DEFAULT '',
                    price REAL DEFAULT 0,
                    description TEXT DEFAULT '',
                    status TEXT DEFAULT 'active',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (cookie_id) REFERENCES cookies(id) ON DELETE CASCADE
                )
            """)

            # Auto-reply rules
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS auto_reply_rules (
                    id TEXT PRIMARY KEY,
                    keyword TEXT NOT NULL,
                    reply_text TEXT NOT NULL,
                    product_id TEXT DEFAULT '',
                    priority INTEGER DEFAULT 0,
                    enabled BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            self.conn.commit()
            logger.info(f"Database initialized: {self.db_path}")

        except Exception as e:
            logger.error(f"Database initialization failed: {e}")
            raise

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Thread-safe execute."""
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute(sql, params)
            self.conn.commit()
            return cursor

    def _fetchone(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute(sql, params)
            return cursor.fetchone()

    def _fetchall(self, sql: str, params: tuple = ()) -> List[sqlite3.Row]:
        with self.lock:
            cursor = self.conn.cursor()
            cursor.execute(sql, params)
            return cursor.fetchall()

    # ---- Cookie Management ----

    def save_cookie(self, cookie_id: str, cookie_value: str, remark: str = ""):
        self._execute(
            "INSERT OR REPLACE INTO cookies (id, value, remark) VALUES (?, ?, ?)",
            (cookie_id, cookie_value, remark),
        )

    def get_cookie(self, cookie_id: str) -> Optional[str]:
        row = self._fetchone("SELECT value FROM cookies WHERE id = ?", (cookie_id,))
        return row["value"] if row else None

    def get_cookie_details(self, cookie_id: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone("SELECT * FROM cookies WHERE id = ?", (cookie_id,))
        return dict(row) if row else None

    def get_all_cookies(self) -> Dict[str, str]:
        rows = self._fetchall("SELECT id, value FROM cookies")
        return {row["id"]: row["value"] for row in rows}

    def delete_cookie(self, cookie_id: str):
        self._execute("DELETE FROM cookies WHERE id = ?", (cookie_id,))

    def update_cookie_value(self, cookie_id: str, new_value: str):
        self._execute("UPDATE cookies SET value = ? WHERE id = ?", (new_value, cookie_id))

    def get_auto_confirm(self, cookie_id: str) -> bool:
        row = self._fetchone("SELECT auto_confirm FROM cookies WHERE id = ?", (cookie_id,))
        return bool(row["auto_confirm"]) if row else True

    def set_auto_confirm(self, cookie_id: str, enabled: bool):
        self._execute("UPDATE cookies SET auto_confirm = ? WHERE id = ?", (int(enabled), cookie_id))

    def get_pause_duration(self, cookie_id: str) -> int:
        row = self._fetchone("SELECT pause_duration FROM cookies WHERE id = ?", (cookie_id,))
        return row["pause_duration"] if row else 10

    # ---- Cookie Status ----

    def get_all_cookie_status(self) -> Dict[str, bool]:
        rows = self._fetchall("SELECT cookie_id, enabled FROM cookie_status")
        return {row["cookie_id"]: bool(row["enabled"]) for row in rows}

    def save_cookie_status(self, cookie_id: str, enabled: bool):
        self._execute(
            "INSERT OR REPLACE INTO cookie_status (cookie_id, enabled, updated_at) VALUES (?, ?, ?)",
            (cookie_id, int(enabled), time.strftime("%Y-%m-%d %H:%M:%S")),
        )

    # ---- Keywords ----

    def save_keywords(self, cookie_id: str, keywords: List[Tuple[str, str]]):
        self._execute("DELETE FROM keywords WHERE cookie_id = ?", (cookie_id,))
        for keyword, reply in keywords:
            self._execute(
                "INSERT INTO keywords (cookie_id, keyword, reply) VALUES (?, ?, ?)",
                (cookie_id, keyword, reply),
            )

    def get_keywords(self, cookie_id: str) -> List[Tuple[str, str]]:
        rows = self._fetchall("SELECT keyword, reply FROM keywords WHERE cookie_id = ?", (cookie_id,))
        return [(row["keyword"], row["reply"]) for row in rows]

    def get_all_keywords(self) -> Dict[str, List[Tuple[str, str]]]:
        rows = self._fetchall("SELECT cookie_id, keyword, reply FROM keywords")
        result: Dict[str, List[Tuple[str, str]]] = {}
        for row in rows:
            result.setdefault(row["cookie_id"], []).append((row["keyword"], row["reply"]))
        return result

    # ---- AI Reply Settings ----

    def get_ai_reply_settings(self, cookie_id: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone("SELECT * FROM ai_reply_settings WHERE cookie_id = ?", (cookie_id,))
        if row:
            d = dict(row)
            d["custom_prompts"] = json.loads(d.get("custom_prompts") or "{}")
            return d
        return None

    def save_ai_reply_settings(self, cookie_id: str, settings: Dict[str, Any]):
        self._execute(
            """INSERT OR REPLACE INTO ai_reply_settings
            (cookie_id, ai_enabled, model_name, api_key, base_url,
             max_discount_percent, max_discount_amount, max_bargain_rounds, custom_prompts, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cookie_id,
                int(settings.get("ai_enabled", False)),
                settings.get("model_name", "deepseek-v4-flash"),
                settings.get("api_key", ""),
                settings.get("base_url", "https://api.deepseek.com"),
                settings.get("max_discount_percent", 10),
                settings.get("max_discount_amount", 100),
                settings.get("max_bargain_rounds", 3),
                json.dumps(settings.get("custom_prompts", {}), ensure_ascii=False),
                time.strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )

    # ---- AI Conversations ----

    def save_conversation(
        self,
        cookie_id: str,
        chat_id: str,
        user_id: str,
        item_id: str,
        role: str,
        content: str,
        intent: str = "",
        bargain_count: int = 0,
    ):
        self._execute(
            """INSERT INTO ai_conversations
            (cookie_id, chat_id, user_id, item_id, role, content, intent, bargain_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (cookie_id, chat_id, user_id, item_id, role, content, intent, bargain_count),
        )

    def get_conversation_history(
        self, cookie_id: str, chat_id: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        rows = self._fetchall(
            """SELECT role, content, intent, bargain_count, created_at
            FROM ai_conversations
            WHERE cookie_id = ? AND chat_id = ?
            ORDER BY id DESC LIMIT ?""",
            (cookie_id, chat_id, limit),
        )
        return [dict(row) for row in reversed(rows)]

    def get_bargain_count(self, cookie_id: str, chat_id: str) -> int:
        row = self._fetchone(
            """SELECT MAX(bargain_count) as max_count FROM ai_conversations
            WHERE cookie_id = ? AND chat_id = ? AND role = 'assistant'""",
            (cookie_id, chat_id),
        )
        return row["max_count"] if row and row["max_count"] else 0

    # ---- Item Cache ----

    def cache_item(self, item_id: str, data: Dict[str, Any], price: float = 0, description: str = ""):
        self._execute(
            "INSERT OR REPLACE INTO ai_item_cache (item_id, data, price, description, last_updated) VALUES (?, ?, ?, ?, ?)",
            (item_id, json.dumps(data, ensure_ascii=False), price, description, time.strftime("%Y-%m-%d %H:%M:%S")),
        )

    def get_cached_item(self, item_id: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone("SELECT * FROM ai_item_cache WHERE item_id = ?", (item_id,))
        if row:
            d = dict(row)
            d["data"] = json.loads(d["data"])
            return d
        return None

    # ---- Orders ----

    def save_order(self, order_id: str, cookie_id: str, **kwargs):
        self._execute(
            """INSERT OR REPLACE INTO orders
            (order_id, cookie_id, item_id, buyer_id, status, amount, delivery_payload, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                order_id,
                cookie_id,
                kwargs.get("item_id", ""),
                kwargs.get("buyer_id", ""),
                kwargs.get("status", "processing"),
                kwargs.get("amount", 0),
                kwargs.get("delivery_payload", ""),
                time.strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )

    def update_order_status(self, order_id: str, status: str):
        self._execute(
            "UPDATE orders SET status = ?, updated_at = ? WHERE order_id = ?",
            (status, time.strftime("%Y-%m-%d %H:%M:%S"), order_id),
        )

    def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone("SELECT * FROM orders WHERE order_id = ?", (order_id,))
        return dict(row) if row else None

    def get_orders_by_cookie(self, cookie_id: str, status: str = None) -> List[Dict[str, Any]]:
        if status:
            rows = self._fetchall(
                "SELECT * FROM orders WHERE cookie_id = ? AND status = ? ORDER BY created_at DESC",
                (cookie_id, status),
            )
        else:
            rows = self._fetchall(
                "SELECT * FROM orders WHERE cookie_id = ? ORDER BY created_at DESC", (cookie_id,)
            )
        return [dict(row) for row in rows]

    # ---- Items ----

    def save_item(self, item_id: str, cookie_id: str, **kwargs):
        self._execute(
            """INSERT OR REPLACE INTO items
            (item_id, cookie_id, title, price, description, status)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (
                item_id,
                cookie_id,
                kwargs.get("title", ""),
                kwargs.get("price", 0),
                kwargs.get("description", ""),
                kwargs.get("status", "active"),
            ),
        )

    def get_items_by_cookie(self, cookie_id: str) -> List[Dict[str, Any]]:
        rows = self._fetchall(
            "SELECT * FROM items WHERE cookie_id = ? ORDER BY created_at DESC", (cookie_id,)
        )
        return [dict(row) for row in rows]

    def get_all_items(self) -> List[Dict[str, Any]]:
        rows = self._fetchall("SELECT * FROM items ORDER BY created_at DESC")
        return [dict(row) for row in rows]

    # ---- Auto-Reply Rules ----

    def save_auto_reply_rule(self, rule_id: str, keyword: str, reply_text: str, **kwargs):
        self._execute(
            """INSERT OR REPLACE INTO auto_reply_rules
            (id, keyword, reply_text, product_id, priority, enabled)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (
                rule_id,
                keyword,
                reply_text,
                kwargs.get("product_id", ""),
                kwargs.get("priority", 0),
                int(kwargs.get("enabled", True)),
            ),
        )

    def get_auto_reply_rules(self, product_id: str = None) -> List[Dict[str, Any]]:
        if product_id:
            rows = self._fetchall(
                "SELECT * FROM auto_reply_rules WHERE (product_id = ? OR product_id = '') AND enabled = 1 ORDER BY priority DESC",
                (product_id,),
            )
        else:
            rows = self._fetchall(
                "SELECT * FROM auto_reply_rules WHERE enabled = 1 ORDER BY priority DESC"
            )
        return [dict(row) for row in rows]

    def delete_auto_reply_rule(self, rule_id: str):
        self._execute("DELETE FROM auto_reply_rules WHERE id = ?", (rule_id,))

    def close(self):
        if self.conn:
            self.conn.close()


# Global singleton
db_manager = DBManager()
