"""WAL/busy_timeout PRAGMA 回归：连接监听必须对每条连接生效。"""
from app.database import engine


def test_sqlite_wal_and_busy_timeout_active():
    conn = engine.raw_connection()
    try:
        cursor = conn.cursor()
        try:
            journal = cursor.execute("PRAGMA journal_mode").fetchone()[0]
            busy = cursor.execute("PRAGMA busy_timeout").fetchone()[0]
        finally:
            cursor.close()
    finally:
        conn.close()
    assert journal == "wal"
    assert busy == 5000
