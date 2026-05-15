"""
SQLite persistence layer.

Tables:
  positions  — currently open bot-managed positions
  trade_log  — completed trade history (entries + exits)
  trend_cache — last trend score per ticker, to throttle Google Trends calls
"""

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "camillo_bot.db"


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class DBPosition:
    ticker:           str
    entry_date:       str          # ISO date string
    entry_price:      float
    notional:         float        # dollar amount invested
    keywords:         list
    entry_score:      float
    signal:           str          # "BUY" | "WATCH"
    half_taken:       bool = False # True once 50% profit-take executed
    last_trend_check: Optional[str] = None   # ISO date of last Google Trends re-scan
    last_trend_score: float = 0.0


@dataclass
class TradeLog:
    ticker:      str
    action:      str    # "ENTRY" | "PARTIAL_EXIT" | "EXIT"
    reason:      str    # "signal" | "stop_loss" | "take_profit" | "trend_peak" | etc.
    price:       float
    notional:    float
    score:       float
    timestamp:   str


# ---------------------------------------------------------------------------
# Database manager
# ---------------------------------------------------------------------------

class Database:
    def __init__(self, path: Path = DB_PATH):
        self.path = path
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._bootstrap()
        log.info("Database ready: %s", path)

    def _bootstrap(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS positions (
                ticker           TEXT PRIMARY KEY,
                entry_date       TEXT NOT NULL,
                entry_price      REAL NOT NULL,
                notional         REAL NOT NULL,
                keywords         TEXT NOT NULL,
                entry_score      REAL NOT NULL,
                signal           TEXT NOT NULL,
                half_taken       INTEGER NOT NULL DEFAULT 0,
                last_trend_check TEXT,
                last_trend_score REAL NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS trade_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker      TEXT    NOT NULL,
                action      TEXT    NOT NULL,
                reason      TEXT    NOT NULL,
                price       REAL    NOT NULL,
                notional    REAL    NOT NULL,
                score       REAL    NOT NULL,
                timestamp   TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS trend_cache (
                ticker       TEXT PRIMARY KEY,
                score        REAL    NOT NULL,
                trend_df_json TEXT,
                updated_at   TEXT    NOT NULL
            );
        """)
        self._conn.commit()

    # ── Positions ──────────────────────────────────────────────────────

    def save_position(self, pos: DBPosition):
        self._conn.execute("""
            INSERT INTO positions
                (ticker, entry_date, entry_price, notional, keywords,
                 entry_score, signal, half_taken, last_trend_check, last_trend_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                entry_price      = excluded.entry_price,
                notional         = excluded.notional,
                entry_score      = excluded.entry_score,
                signal           = excluded.signal,
                half_taken       = excluded.half_taken,
                last_trend_check = excluded.last_trend_check,
                last_trend_score = excluded.last_trend_score
        """, (
            pos.ticker, pos.entry_date, pos.entry_price, pos.notional,
            json.dumps(pos.keywords), pos.entry_score, pos.signal,
            int(pos.half_taken), pos.last_trend_check, pos.last_trend_score,
        ))
        self._conn.commit()

    def get_position(self, ticker: str) -> Optional[DBPosition]:
        row = self._conn.execute(
            "SELECT * FROM positions WHERE ticker = ?", (ticker,)
        ).fetchone()
        return self._row_to_position(row) if row else None

    def get_all_positions(self) -> list:
        rows = self._conn.execute("SELECT * FROM positions").fetchall()
        return [self._row_to_position(r) for r in rows]

    def mark_half_taken(self, ticker: str):
        self._conn.execute(
            "UPDATE positions SET half_taken = 1 WHERE ticker = ?", (ticker,)
        )
        self._conn.commit()

    def update_trend_check(self, ticker: str, score: float):
        today = date.today().isoformat()
        self._conn.execute("""
            UPDATE positions
            SET last_trend_check = ?, last_trend_score = ?
            WHERE ticker = ?
        """, (today, score, ticker))
        self._conn.commit()

    def close_position(self, ticker: str):
        self._conn.execute("DELETE FROM positions WHERE ticker = ?", (ticker,))
        self._conn.commit()

    def _row_to_position(self, row) -> DBPosition:
        return DBPosition(
            ticker           = row["ticker"],
            entry_date       = row["entry_date"],
            entry_price      = row["entry_price"],
            notional         = row["notional"],
            keywords         = json.loads(row["keywords"]),
            entry_score      = row["entry_score"],
            signal           = row["signal"],
            half_taken       = bool(row["half_taken"]),
            last_trend_check = row["last_trend_check"],
            last_trend_score = row["last_trend_score"],
        )

    # ── Trade log ─────────────────────────────────────────────────────

    def log_trade(self, trade: TradeLog):
        self._conn.execute("""
            INSERT INTO trade_log (ticker, action, reason, price, notional, score, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            trade.ticker, trade.action, trade.reason,
            trade.price, trade.notional, trade.score, trade.timestamp,
        ))
        self._conn.commit()
        log.info("Trade logged: %s %s %s $%.2f", trade.action, trade.ticker, trade.reason, trade.notional)

    def get_trade_history(self, ticker: str = None) -> list:
        if ticker:
            rows = self._conn.execute(
                "SELECT * FROM trade_log WHERE ticker = ? ORDER BY id", (ticker,)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM trade_log ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_summary(self) -> dict:
        """Quick P&L summary from trade log."""
        rows = self._conn.execute("""
            SELECT
                COUNT(DISTINCT ticker)                              as tickers_traded,
                SUM(CASE WHEN action = 'ENTRY' THEN notional END)  as total_deployed,
                SUM(CASE WHEN action = 'EXIT' OR action = 'PARTIAL_EXIT'
                         THEN notional END)                         as total_returned,
                COUNT(CASE WHEN action = 'EXIT' THEN 1 END)        as exits
            FROM trade_log
        """).fetchone()
        return dict(rows) if rows else {}
