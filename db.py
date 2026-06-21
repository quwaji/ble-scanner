import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _init(conn)
    return conn


def _init(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS devices (
            mac            TEXT PRIMARY KEY,
            name           TEXT,
            first_seen     TEXT NOT NULL,
            last_seen      TEXT NOT NULL,
            scan_count     INTEGER NOT NULL DEFAULT 1,
            last_rssi      INTEGER,
            lost_notified  INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS scan_events (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            mac       TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            rssi      INTEGER
        );

        CREATE TABLE IF NOT EXISTS scan_sessions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp    TEXT NOT NULL,
            device_count INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS app_state (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    # migrate existing DB that lacks lost_notified column
    cols = [row[1] for row in conn.execute("PRAGMA table_info(devices)")]
    if "lost_notified" not in cols:
        conn.execute("ALTER TABLE devices ADD COLUMN lost_notified INTEGER NOT NULL DEFAULT 0")
    conn.execute(
        "INSERT OR IGNORE INTO app_state (key, value) VALUES ('session_start', ?)",
        (now_iso(),),
    )
    conn.commit()


def upsert_device(conn: sqlite3.Connection, mac: str, name: str | None, rssi: int | None) -> bool:
    """Insert or update a device. Returns True if the device is new.
    Resets lost_notified when a previously lost device reappears."""
    ts = now_iso()
    existing = conn.execute("SELECT mac FROM devices WHERE mac = ?", (mac,)).fetchone()
    if existing is None:
        conn.execute(
            "INSERT INTO devices (mac, name, first_seen, last_seen, scan_count, last_rssi, lost_notified) "
            "VALUES (?, ?, ?, ?, 1, ?, 0)",
            (mac, name, ts, ts, rssi),
        )
        conn.commit()
        return True
    else:
        conn.execute(
            "UPDATE devices SET name = COALESCE(?, name), last_seen = ?, "
            "scan_count = scan_count + 1, last_rssi = ?, lost_notified = 0 WHERE mac = ?",
            (name, ts, rssi, mac),
        )
        conn.commit()
        return False


def record_scan_event(conn: sqlite3.Connection, mac: str, rssi: int | None) -> None:
    conn.execute(
        "INSERT INTO scan_events (mac, timestamp, rssi) VALUES (?, ?, ?)",
        (mac, now_iso(), rssi),
    )
    conn.commit()


def get_newly_lost_devices(conn: sqlite3.Connection, threshold_seconds: int) -> list[sqlite3.Row]:
    """Return devices that exceeded the lost threshold and haven't been notified yet."""
    cutoff = datetime.now(timezone.utc).timestamp() - threshold_seconds
    rows = conn.execute(
        "SELECT mac, name, last_seen FROM devices WHERE lost_notified = 0"
    ).fetchall()
    lost = []
    for row in rows:
        last = datetime.fromisoformat(row["last_seen"]).timestamp()
        if last < cutoff:
            lost.append(row)
    return lost


def mark_lost_notified(conn: sqlite3.Connection, mac: str) -> None:
    conn.execute("UPDATE devices SET lost_notified = 1 WHERE mac = ?", (mac,))
    conn.commit()


def record_scan_session(conn: sqlite3.Connection, device_count: int) -> None:
    conn.execute(
        "INSERT INTO scan_sessions (timestamp, device_count) VALUES (?, ?)",
        (now_iso(), device_count),
    )
    conn.commit()


def get_stats(conn: sqlite3.Connection) -> dict:
    session_start = conn.execute(
        "SELECT value FROM app_state WHERE key = 'session_start'"
    ).fetchone()["value"]

    total = conn.execute(
        "SELECT COUNT(DISTINCT mac) as c FROM scan_events WHERE timestamp >= ?",
        (session_start,),
    ).fetchone()["c"]

    peak_row = conn.execute(
        "SELECT device_count, timestamp FROM scan_sessions"
        " WHERE timestamp >= ? ORDER BY device_count DESC, timestamp DESC LIMIT 1",
        (session_start,),
    ).fetchone()

    latest_row = conn.execute(
        "SELECT device_count, timestamp FROM scan_sessions ORDER BY id DESC LIMIT 1"
    ).fetchone()

    return {
        "session_start": session_start,
        "total_devices": total,
        "peak_count": peak_row["device_count"] if peak_row else 0,
        "peak_time": peak_row["timestamp"] if peak_row else None,
        "latest_count": latest_row["device_count"] if latest_row else 0,
        "latest_time": latest_row["timestamp"] if latest_row else None,
    }


def get_scan_history(conn: sqlite3.Connection, hours: int = 3) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    rows = conn.execute(
        "SELECT timestamp, device_count FROM scan_sessions WHERE timestamp >= ? ORDER BY timestamp",
        (cutoff,),
    ).fetchall()
    return [{"t": row["timestamp"], "c": row["device_count"]} for row in rows]


def reset_session(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE app_state SET value = ? WHERE key = 'session_start'", (now_iso(),))
    conn.commit()
