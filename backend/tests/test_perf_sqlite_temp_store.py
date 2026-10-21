"""性能基线暴露的 SQLite 回归：临时库落盘失败导致大排序分页 500。

真实故障（perf_probe 全量播种 5000 条任务后复现）：
`/api/tasks?page=250&page_size=20` 返回 500，后端日志是
`sqlite3.OperationalError: unable to open database file`。
根因不在分页代码：ORDER BY created_at DESC + 大 OFFSET 需要 SQLite 把 offset+limit 行
塞进排序器，超过排序器内存上限后写临时文件；本机 Windows 的 TEMP 目录不可写
（mkstemp 返回 PermissionError），于是 SQLITE_CANTOPEN 直接穿透成 500。
修复见 app/database.py 的连接级 `PRAGMA temp_store=2`。
"""
from sqlalchemy import text

from app.database import engine
from app.models import Task


def test_sqlite_connection_uses_in_memory_temp_store():
    """连接级 PRAGMA 必须生效——删掉监听器时本用例先失败。"""
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA temp_store")).scalar() == 2


def test_deep_pagination_on_large_sorted_table_returns_200(client):
    """深翻页（排序器需缓冲全部前序行）不得因临时文件失败而 500。"""
    fat = "报销凭证核对说明-" + "x" * 600  # 单行 >600B，3000 行足够触发落盘
    rows = [
        {
            "title": f"排序压力样本 {i:05d}",
            "description": fat,
            "status": "todo",
            "priority": "medium",
            "category": "税务",
        }
        for i in range(3000)
    ]
    table = Task.__table__
    with engine.begin() as conn:
        for i in range(0, len(rows), 1000):
            conn.execute(table.insert(), rows[i : i + 1000])

    resp = client.get("/api/tasks", params={"page": 149, "page_size": 20})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 3000
    assert len(body["items"]) == 20

    # 尾页之后的空页也要正常返回（历史上这里同样是 500）
    tail = client.get("/api/tasks", params={"page": 300, "page_size": 20})
    assert tail.status_code == 200
    assert tail.json()["items"] == []


def test_notes_sorted_by_updated_at_deep_page_returns_200(client):
    """笔记列表默认按 updated_at 排序，同一条失败路径的第二个入口。"""
    from app.models import Note

    rows = [
        {
            "title": f"笔记排序样本 {i:05d}",
            "content": "会议纪要正文-" + "y" * 600,
            "tags": ["税务"],
            "pinned": False,
        }
        for i in range(2000)
    ]
    with engine.begin() as conn:
        for i in range(0, len(rows), 1000):
            conn.execute(Note.__table__.insert(), rows[i : i + 1000])

    resp = client.get("/api/notes", params={"page": 99, "page_size": 20})
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 2000
