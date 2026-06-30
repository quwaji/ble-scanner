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


def record_window(
    conn: sqlite3.Connection,
    detected: dict[str, tuple[str | None, int | None]],
    lost_threshold_seconds: int,
) -> tuple[set[str], list[sqlite3.Row]]:
    """Persist one scan window in a single transaction.

    Committing once per device made the cost of a window scale with the number
    of devices; on slow hardware that let recording outrun the scan interval and
    snowball (the window kept growing, so each window had even more devices).
    Batching every insert/update into one transaction keeps a window to a single
    commit regardless of how many devices were seen.

    Returns (new_macs, newly_lost_rows) so the caller can log them.
    """
    ts = now_iso()
    macs = list(detected)

    # Which of these MACs are already known? Look them up in chunks to stay well
    # under SQLite's bound-parameter limit.
    existing: set[str] = set()
    for i in range(0, len(macs), 500):
        chunk = macs[i:i + 500]
        placeholders = ",".join("?" * len(chunk))
        existing.update(
            row[0]
            for row in conn.execute(
                f"SELECT mac FROM devices WHERE mac IN ({placeholders})", chunk
            )
        )
    new_macs = {mac for mac in macs if mac not in existing}

    inserts = [(mac, detected[mac][0], ts, ts, detected[mac][1]) for mac in new_macs]
    updates = [(detected[mac][0], ts, detected[mac][1], mac) for mac in existing]
    events = [(mac, ts, detected[mac][1]) for mac in macs]

    cutoff = (
        datetime.now(timezone.utc) - timedelta(seconds=lost_threshold_seconds)
    ).isoformat()

    with conn:  # one transaction -> one commit
        if inserts:
            conn.executemany(
                "INSERT INTO devices (mac, name, first_seen, last_seen, scan_count, last_rssi, lost_notified) "
                "VALUES (?, ?, ?, ?, 1, ?, 0)",
                inserts,
            )
        if updates:
            conn.executemany(
                "UPDATE devices SET name = COALESCE(?, name), last_seen = ?, "
                "scan_count = scan_count + 1, last_rssi = ?, lost_notified = 0 WHERE mac = ?",
                updates,
            )
        if events:
            conn.executemany(
                "INSERT INTO scan_events (mac, timestamp, rssi) VALUES (?, ?, ?)",
                events,
            )
        conn.execute(
            "INSERT INTO scan_sessions (timestamp, device_count) VALUES (?, ?)",
            (ts, len(detected)),
        )
        # Devices seen this window just had last_seen bumped to ts, so only
        # genuinely absent devices fall before the cutoff. last_seen is an
        # ISO-8601 UTC string, so a lexicographic compare matches a chronological
        # one.
        lost = conn.execute(
            "SELECT mac, name, last_seen FROM devices "
            "WHERE lost_notified = 0 AND last_seen < ?",
            (cutoff,),
        ).fetchall()
        if lost:
            conn.executemany(
                "UPDATE devices SET lost_notified = 1 WHERE mac = ?",
                [(row["mac"],) for row in lost],
            )

    return new_macs, lost


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
