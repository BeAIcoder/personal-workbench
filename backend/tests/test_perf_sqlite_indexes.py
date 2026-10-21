"""性能基线暴露的索引回归：模型加了索引，但历史库拿不到。

perf_probe 在 5000 任务 / 2000 笔记 / 200 会话的库上跑出的执行计划里，
三条最热的排序查询全是「全表扫描 + 临时 B 树」：
  - tasks：ORDER BY created_at DESC（任务列表默认序）
  - notes：ORDER BY pinned DESC, updated_at DESC（笔记列表默认序）
  - agent_sessions：ORDER BY updated_at DESC（会话列表）
补上模型声明后还有第二个坑：`Base.metadata.create_all` 只建新表，不会给**已存在的表**
加索引，所以老用户的库即便升级代码也仍然走全表扫描。修复见 app/main.py 的
`_ensure_sqlite_indexes()`（启动时按元数据补齐缺失索引）。
"""
from sqlalchemy import inspect, text

from app.database import Base, engine
from app.main import _ensure_sqlite_indexes
from app.models import AgentSession, Note, Task


def _index_names(table_name: str) -> set[str]:
    """列出表上的索引名。表不存在时直接报错——否则「空集合」会让断言假通过。

    PRAGMA index_list 的列序是 (seq, name, unique, origin, partial)，名字在第 2 列。
    """
    with engine.connect() as conn:
        if not [r for r in conn.execute(text(f"PRAGMA table_info({table_name})"))]:
            raise AssertionError(f"表 {table_name} 不存在，测试前置未建表")
        return {row[1] for row in conn.execute(text(f"PRAGMA index_list({table_name})"))}


def test_models_declare_the_hot_list_ordering_indexes():
    """列表排序列必须有索引声明——删掉 index=True / Index() 时本用例先失败。"""
    assert {c.name for c in Task.__table__.indexes} >= {"ix_tasks_created_at", "ix_tasks_completed_at"}
    assert "ix_notes_pinned_updated" in {c.name for c in Note.__table__.indexes}
    assert "ix_agent_sessions_updated_at" in {c.name for c in AgentSession.__table__.indexes}


def test_ensure_sqlite_indexes_adds_missing_index_to_existing_table(client):
    """核心场景：表已存在但缺索引（老库升级），启动补齐后必须建出来。"""
    table_name = Task.__tablename__
    assert "ix_tasks_created_at" in _index_names(table_name)  # 前置：新库建表时已带索引
    with engine.begin() as conn:
        conn.execute(text("DROP INDEX ix_tasks_created_at"))
    assert "ix_tasks_created_at" not in _index_names(table_name)

    _ensure_sqlite_indexes()

    assert "ix_tasks_created_at" in _index_names(table_name)


def test_ensure_sqlite_indexes_is_idempotent(client):
    """重复启动不得报错，也不得重复建索引（等价 CREATE INDEX IF NOT EXISTS）。"""
    before = _index_names(Note.__tablename__)
    _ensure_sqlite_indexes()
    _ensure_sqlite_indexes()
    assert _index_names(Note.__tablename__) == before
    assert "ix_notes_pinned_updated" in before


def test_task_list_ordering_uses_the_index_not_a_temp_btree(client):
    """补齐后，任务列表默认排序应走索引扫描，不再出现 USE TEMP B-TREE FOR ORDER BY。"""
    rows = [
        {
            "title": f"索引验证 {i:05d}",
            "description": "",
            "status": "todo",
            "priority": "medium",
            "category": "税务",
        }
        for i in range(200)
    ]
    with engine.begin() as conn:
        conn.execute(Task.__table__.insert(), rows)

    with engine.connect() as conn:
        plan = [
            row[3]
            for row in conn.execute(
                text("EXPLAIN QUERY PLAN SELECT * FROM tasks ORDER BY created_at DESC LIMIT 20 OFFSET 180")
            )
        ]
    assert not any("USE TEMP B-TREE" in p for p in plan), plan
    assert any("ix_tasks_created_at" in p for p in plan), plan


def test_metadata_indexes_all_exist_after_startup(client):
    """元数据里声明的每一个索引，启动补齐后都应真实存在（防止漏建）。"""
    _ensure_sqlite_indexes()
    inspector = inspect(engine)
    missing = []
    checked = 0
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        actual = _index_names(table.name)
        for index in table.indexes:
            checked += 1
            if index.name not in actual:
                missing.append(f"{table.name}.{index.name}")
    assert missing == []
    # 断言检查面非空：若一个索引都没检查到，说明前置建表没生效
    assert checked > 10, f"仅检查到 {checked} 个索引声明，前置建表可能未生效"


def test_notes_list_endpoint_still_ok_with_composite_index(client):
    """接口层兜底：加了复合索引后笔记列表行为不变（排序仍是置顶优先）。"""
    for i, pinned in enumerate((False, True, False)):
        client.post("/api/notes", json={"title": f"索引-笔记 {i}", "pinned": pinned})
    items = client.get("/api/notes", params={"page_size": 10}).json()["items"]
    assert items[0]["pinned"] is True, f"置顶笔记未排在首位：{[n['title'] for n in items]}"
