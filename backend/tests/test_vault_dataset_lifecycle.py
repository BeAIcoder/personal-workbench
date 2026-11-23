"""数据池数据集生命周期与聚合另存覆盖语义（T4C）。

全部使用合成数据：DuckDB 库文件经 monkeypatch 重定向到 tmp_path，SQLite 走
conftest 的临时库；不触真实 backend/data，不调外部服务。

覆盖：
- 聚合另存目标已存在默认 DatasetConflict（HTTP 409），allow_overwrite 才覆盖；
- 清洗应用同口径（已存在结果表 409，不再静默 replace）；
- 数据集使用情况预检（物理修订表/视图/登记一致性与清洗日志引用）；
- 安全删除：二次确认（confirm=数据集名）、引用拦截、force 清理孤儿与不一致状态；
- 安全重命名：视图 + 物理修订表 + 元数据 + 清洗日志引用同步，冲突/非法名拒绝；
- 入仓登记与视图/物理表写入同处一个写锁（与删除/重命名串行），登记失败回滚 DuckDB；
- 清洗/聚合另存的目标检查与清洗日志写入同处写锁，并发双检查只放行一个、日志不指向已删目标。
"""
import contextlib
import threading

import pandas as pd
import pytest

from app.database import Base, engine
from app.modules.finance_vault import cleaning as cl
from app.modules.finance_vault import service as svc


@pytest.fixture(autouse=True)
def _ensure_tables():
    """非 client 用例紧随 client 重文件之后运行时必须自建表（conftest 只 drop 不重建）。"""
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture()
def vault_dir(monkeypatch, tmp_path):
    """DuckDB 库文件重定向到 tmp_path（测试不写真实数据目录）。"""
    base = tmp_path / "vault_data"
    monkeypatch.setattr(svc, "get_finance_data_dir", lambda: base)
    return base


def _df() -> pd.DataFrame:
    return pd.DataFrame({
        "月份": ["2026-09", "2026-09", "2026-10"],
        "金额": [100.0, 200.0, 150.0],
    })


def _aggregate_spec() -> dict:
    return {"group_by": ["月份"], "metrics": [{"column": "金额", "agg": "sum"}]}


def _table_names() -> set[str]:
    return {t["table"] for t in svc.list_datasets()}


def _drop_view_directly(table: str) -> None:
    """模拟数据仓视图损坏（登记表还在）：用于不一致状态预检/删除修复。"""
    with svc._write_lock:
        con = svc._connect()
        try:
            con.execute(f'DROP VIEW IF EXISTS "{table}"')
        finally:
            con.close()


# ---------------- 聚合另存：默认 409，不静默覆盖 ----------------

def test_aggregate_save_conflicts_without_allow_overwrite(vault_dir):
    svc.ingest_dataframe("agg_src", _df())
    first = cl.save_aggregate_as_dataset("agg_src", _aggregate_spec(), "agg_out")
    assert first["result_table"] == "agg_out" and first["rows"] == 2

    # 目标已存在：默认拒绝，且旧结果原样保留（不得静默 replace）
    with pytest.raises(svc.DatasetConflict):
        cl.save_aggregate_as_dataset("agg_src", {"metrics": [{"column": "*", "agg": "count"}]}, "agg_out")
    kept = next(t for t in svc.list_datasets() if t["table"] == "agg_out")
    assert {c["name"] for c in kept["columns"]} == {"月份", "金额_sum"}
    assert svc.run_query("SELECT count(*) AS n FROM agg_src")["rows"][0][0] == 3

    # 显式覆盖：结果按新配方重算
    over = cl.save_aggregate_as_dataset(
        "agg_src", {"metrics": [{"column": "*", "agg": "count"}]},
        "agg_out", allow_overwrite=True,
    )
    assert over["result_table"] == "agg_out"
    q = svc.run_query("SELECT * FROM agg_out")
    assert q["columns"] == ["总行数_count"] and q["rows"] == [[3]]


def test_aggregate_save_self_target_requires_allow_overwrite(vault_dir):
    svc.ingest_dataframe("agg_self", _df())
    with pytest.raises(svc.DatasetConflict):
        cl.save_aggregate_as_dataset("agg_self", _aggregate_spec(), "agg_self")
    # 覆盖到自己：聚合结果替换原数据（显式授权才允许）
    cl.save_aggregate_as_dataset("agg_self", _aggregate_spec(), "agg_self", allow_overwrite=True)
    assert svc.run_query("SELECT count(*) AS n FROM agg_self")["rows"][0][0] == 2


def test_clean_apply_conflict_is_dataset_conflict(vault_dir):
    svc.ingest_dataframe("clean_src", _df())
    cl.clean_apply("clean_src", None, target_table="clean_out")
    with pytest.raises(svc.DatasetConflict):
        cl.clean_apply("clean_src", None, target_table="clean_out")
    # 目标未被静默覆盖：行数仍是首次清洗结果
    assert svc.run_query("SELECT count(*) AS n FROM clean_out")["rows"][0][0] == 3


# ---------------- 使用情况预检（二次确认所需接口） ----------------

def test_dataset_usage_reports_physical_tables_and_references(vault_dir):
    svc.ingest_dataframe("usage_src", _df())
    svc.ingest_dataframe("usage_src", _df(), mode="replace")  # 两个物理修订表
    cl.clean_apply("usage_src", None, target_table="usage_out")

    usage = svc.dataset_usage("usage_src")
    assert usage["table"] == "usage_src"
    assert usage["registered"] is True and usage["view_exists"] is True
    assert usage["physical_tables"] == ["usage_src__r1", "usage_src__r2"]
    assert usage["rows"] == 3
    assert usage["consistent"] is True and usage["issues"] == []
    assert usage["references"] == {"clean_logs": 1, "as_source": 1, "as_result": 0}
    assert usage["requires_force"] is True
    assert usage["confirm_name"] == "usage_src"

    out_usage = svc.dataset_usage("usage_out")
    assert out_usage["references"] == {"clean_logs": 1, "as_source": 0, "as_result": 1}
    assert out_usage["requires_force"] is True

    ghost = svc.dataset_usage("ghost_absent")
    assert ghost["registered"] is False and ghost["view_exists"] is False
    assert ghost["physical_tables"] == []
    assert ghost["consistent"] is True and ghost["issues"] == []
    assert ghost["requires_force"] is False


def test_dataset_usage_flags_metadata_view_drift(vault_dir):
    svc.ingest_dataframe("drift", _df())
    _drop_view_directly("drift")
    usage = svc.dataset_usage("drift")
    assert usage["registered"] is True and usage["view_exists"] is False
    assert usage["consistent"] is False
    assert usage["issues"] and "视图" in usage["issues"][0]
    assert usage["requires_force"] is False  # 无引用：可直接删除修复


# ---------------- 安全删除 ----------------

def test_delete_requires_exact_second_confirmation(vault_dir):
    svc.ingest_dataframe("del_confirm", _df())
    with pytest.raises(ValueError):
        svc.delete_dataset("del_confirm", "")
    with pytest.raises(ValueError):
        svc.delete_dataset("del_confirm", "del_confir")
    assert "del_confirm" in _table_names()
    with pytest.raises(ValueError):
        svc.delete_dataset("Bad-Name", "Bad-Name")
    with pytest.raises(LookupError):
        svc.delete_dataset("never_registered", "never_registered")


def test_delete_referenced_dataset_requires_force(vault_dir):
    svc.ingest_dataframe("del_src", _df())
    cl.clean_apply("del_src", None, target_table="del_out")

    with pytest.raises(svc.DatasetConflict):
        svc.delete_dataset("del_src", "del_src")
    assert "del_src" in _table_names()

    result = svc.delete_dataset("del_src", "del_src", force=True)
    assert result["deleted"] is True and result["table"] == "del_src"
    assert result["dropped_physical"] == 1 and result["metadata_only"] is False
    assert "del_src" not in _table_names() and "del_out" in _table_names()
    # 视图 + 物理修订表都清理干净，只读查询也拒绝
    usage = svc.dataset_usage("del_src")
    assert usage["view_exists"] is False and usage["physical_tables"] == []
    with pytest.raises(svc.SqlRejected):
        svc.run_query("SELECT * FROM del_src")


def test_delete_removes_all_physical_revisions(vault_dir):
    svc.ingest_dataframe("del_rev", _df())
    svc.ingest_dataframe("del_rev", _df(), mode="replace")
    usage = svc.dataset_usage("del_rev")
    assert len(usage["physical_tables"]) == 2

    result = svc.delete_dataset("del_rev", "del_rev")
    assert result["dropped_physical"] == 2
    assert svc.dataset_usage("del_rev")["physical_tables"] == []


def test_delete_repairs_metadata_only_registration(vault_dir):
    svc._register_dataset("meta_only", source="test", rows=0, columns=[])
    result = svc.delete_dataset("meta_only", "meta_only")
    assert result["metadata_only"] is True and result["dropped_physical"] == 0
    assert "meta_only" not in _table_names()


def test_delete_orphan_physical_tables_requires_force(vault_dir):
    with svc._write_lock:
        con = svc._connect()
        try:
            con.execute('CREATE TABLE orphan__r1 AS SELECT 1 AS a')
        finally:
            con.close()

    with pytest.raises(svc.DatasetConflict):
        svc.delete_dataset("orphan", "orphan")
    assert svc.dataset_usage("orphan")["physical_tables"] == ["orphan__r1"]

    result = svc.delete_dataset("orphan", "orphan", force=True)
    assert result["deleted"] is True and result["dropped_physical"] == 1
    assert svc.dataset_usage("orphan")["physical_tables"] == []


def test_delete_orphan_view_requires_force(vault_dir):
    with svc._write_lock:
        con = svc._connect()
        try:
            con.sql("SELECT 1 AS a").to_view("orphan_view")
        finally:
            con.close()

    usage = svc.dataset_usage("orphan_view")
    assert usage["registered"] is False and usage["view_exists"] is True
    assert usage["requires_force"] is True and usage["consistent"] is False
    with pytest.raises(svc.DatasetConflict):
        svc.delete_dataset("orphan_view", "orphan_view")
    result = svc.delete_dataset("orphan_view", "orphan_view", force=True)
    assert result["deleted"] is True
    assert svc.dataset_usage("orphan_view")["view_exists"] is False


def test_drop_relation_rejects_unvalidated_identifier(vault_dir):
    """DROP 只接受白名单标识符：调用方原文绝不拼进 SQL。"""
    con = svc._connect()
    try:
        with pytest.raises(ValueError):
            svc._drop_relation(con, 'x"; DROP TABLE keep_me; --', "VIEW")
        con.execute('CREATE TABLE keep_me AS SELECT 1 AS a')
        with pytest.raises(ValueError):
            svc._drop_relation(con, 'keep_me"', "TABLE")
        assert con.execute("SELECT count(*) FROM keep_me").fetchone()[0] == 1
        con.execute('DROP TABLE keep_me')
    finally:
        con.close()


# ---------------- 安全重命名 ----------------

def test_rename_moves_view_physical_tables_and_metadata(vault_dir):
    svc.ingest_dataframe("old_name", _df())
    svc.ingest_dataframe("old_name", _df(), mode="replace")
    cl.clean_apply("old_name", None, target_table="old_clean")

    result = svc.rename_dataset("old_name", "new_name")
    assert result["old_table"] == "old_name" and result["table"] == "new_name"
    assert result["physical_tables"] == ["new_name__r1", "new_name__r2"]
    assert result["rewritten_logs"] == 1
    assert result["rewritten_sources"] == 1  # old_clean 的来源文案「清洗自 old_name」

    assert "new_name" in _table_names() and "old_name" not in _table_names()
    assert svc.run_query("SELECT count(*) AS n FROM new_name")["rows"][0][0] == 3
    with pytest.raises(svc.SqlRejected):
        svc.run_query("SELECT * FROM old_name")

    # 物理表跟名字走：旧名无残留，新名两个修订
    assert svc.dataset_usage("old_name")["physical_tables"] == []
    usage = svc.dataset_usage("new_name")
    assert usage["physical_tables"] == ["new_name__r1", "new_name__r2"]
    assert usage["consistent"] is True

    # 元数据（行数/列）随行迁移，append 仍落在重命名后的物理表
    row = next(t for t in svc.list_datasets() if t["table"] == "new_name")
    assert row["rows"] == 3 and {c["name"] for c in row["columns"]} == {"月份", "金额"}
    svc.ingest_dataframe("new_name", pd.DataFrame({"月份": ["2026-11"], "金额": [50.0]}), mode="append")
    assert svc.run_query("SELECT count(*) AS n FROM new_name")["rows"][0][0] == 4
    assert svc.dataset_usage("new_name")["physical_tables"] == ["new_name__r1", "new_name__r2"]

    # 清洗日志引用同步改名（历史配方仍能按新名查回来）
    assert cl.get_clean_logs("new_name")
    assert cl.get_clean_logs("new_name")[0]["source_table"] == "new_name"
    assert cl.get_clean_logs("old_name") == []
    # 其它数据集的来源文案同步（不清洗也不改历史配方 JSON）
    src_row = next(t for t in svc.list_datasets() if t["table"] == "old_clean")
    assert src_row["source"] == "清洗自 new_name"


def test_rename_rejects_conflicts_and_bad_names(vault_dir):
    svc.ingest_dataframe("ren_a", _df())
    svc.ingest_dataframe("ren_b", _df())

    with pytest.raises(svc.DatasetConflict):
        svc.rename_dataset("ren_a", "ren_b")
    with pytest.raises(ValueError):
        svc.rename_dataset("ren_a", "ren_a")
    with pytest.raises(ValueError):
        svc.rename_dataset("ren_a", "Bad-Name")
    with pytest.raises(ValueError):
        svc.rename_dataset("ren_a", "read_csv")  # 引擎保留表函数名
    with pytest.raises(LookupError):
        svc.rename_dataset("ren_missing", "ren_c")

    # 失败后原表仍可查询，目标表未被波及
    assert svc.run_query("SELECT count(*) AS n FROM ren_a")["rows"][0][0] == 3
    assert svc.run_query("SELECT count(*) AS n FROM ren_b")["rows"][0][0] == 3


def test_rename_rejects_inconsistent_source(vault_dir):
    svc.ingest_dataframe("ren_drift", _df())
    _drop_view_directly("ren_drift")
    with pytest.raises(svc.DatasetConflict):
        svc.rename_dataset("ren_drift", "ren_drift_new")
    assert "ren_drift" in _table_names()


def test_rename_syncs_source_provenance_text(vault_dir):
    svc.ingest_dataframe("tok_src", _df())
    svc.ingest_dataframe("tok_src_extra", _df())
    svc._register_dataset("tok_derived", source="清洗自 tok_src_extra 与 tok_src", rows=0, columns=[])

    result = svc.rename_dataset("tok_src", "tok_new")
    assert result["rewritten_sources"] == 1
    row = next(t for t in svc.list_datasets() if t["table"] == "tok_derived")
    # 只替换完整标识符：tok_src_extra 不能被误改
    assert row["source"] == "清洗自 tok_src_extra 与 tok_new"


# ---------------- 长名派生标识符（复核 I-1） ----------------

def test_long_name_dataset_usage_and_delete(vault_dir):
    name = "a" * 64
    svc.ingest_dataframe(name, _df())
    usage = svc.dataset_usage(name)
    assert usage["physical_tables"] == [name + "__r1"]
    assert len(usage["physical_tables"][0]) == 68  # 派生名超过数据集名 64 上限，仍须可识别可删除

    result = svc.delete_dataset(name, name)
    assert result["deleted"] is True and result["dropped_physical"] == 1
    assert svc.dataset_usage(name)["view_exists"] is False
    assert svc.dataset_usage(name)["physical_tables"] == []


def test_long_name_rename_then_delete(vault_dir):
    old, new = "b" * 61, "c" * 64
    svc.ingest_dataframe(old, _df())
    result = svc.rename_dataset(old, new)
    assert result["physical_tables"] == [new + "__r1"]
    assert svc.run_query(f'SELECT count(*) AS n FROM "{new}"')["rows"][0][0] == 3

    assert svc.delete_dataset(new, new)["dropped_physical"] == 1
    assert svc.dataset_usage(new)["physical_tables"] == []


def test_delete_validates_every_target_before_dropping(vault_dir):
    name = "g" * 64
    svc.ingest_dataframe(name, _df())
    bad_physical = name + "__r" + "9" * 10  # 78 字符、修订号 10 位：非法派生名
    with svc._write_lock:
        con = svc._connect()
        try:
            con.execute(f'CREATE TABLE "{bad_physical}" AS SELECT 1 AS a')
        finally:
            con.close()

    with pytest.raises(ValueError):
        svc.delete_dataset(name, name)
    # 全量校验先于任何 DROP：视图与正常物理表都还在
    assert svc.run_query(f'SELECT count(*) AS n FROM "{name}"')["rows"][0][0] == 3
    assert svc.dataset_usage(name)["physical_tables"] == [name + "__r1", bad_physical]


def test_physical_tables_ignore_views_and_revision_names(vault_dir):
    svc.ingest_dataframe("rev_src", _df())
    with svc._write_lock:
        con = svc._connect()
        try:
            con.sql("SELECT 1 AS a").to_view("rev_src__r9")
        finally:
            con.close()

    assert svc.dataset_usage("rev_src")["physical_tables"] == ["rev_src__r1"]
    # 视图不算修订：后续 replace 仍推进到 r2（不是 r10）
    svc.ingest_dataframe("rev_src", _df(), mode="replace")
    assert svc.dataset_usage("rev_src")["physical_tables"] == ["rev_src__r1", "rev_src__r2"]


def test_rename_target_base_table_conflicts(vault_dir):
    svc.ingest_dataframe("bt_src", _df())
    with svc._write_lock:
        con = svc._connect()
        try:
            con.execute("CREATE TABLE bt_taken AS SELECT 1 AS a")
        finally:
            con.close()

    usage = svc.dataset_usage("bt_taken")
    assert usage["registered"] is False and usage["relation_kind"] == "BASE TABLE"
    assert usage["requires_force"] is True and usage["consistent"] is False

    with pytest.raises(svc.DatasetConflict):
        svc.rename_dataset("bt_src", "bt_taken")
    assert svc.run_query("SELECT count(*) AS n FROM bt_src")["rows"][0][0] == 3

    # 同名 BASE TABLE 可由 force 删除清理
    assert svc.delete_dataset("bt_taken", "bt_taken", force=True)["dropped_physical"] == 1
    assert svc.dataset_usage("bt_taken")["relation_kind"] == ""


# ---------------- 重命名回滚（复核 I-2） ----------------

def test_rename_rolls_back_when_view_creation_fails(vault_dir, monkeypatch):
    svc.ingest_dataframe("rb_view", _df())

    def boom(con, table):
        raise RuntimeError("注入失败：建视图")

    with monkeypatch.context() as mp:
        mp.setattr(svc, "_create_dataset_view", boom)
        with pytest.raises(RuntimeError):
            svc.rename_dataset("rb_view", "rb_view_new")

    assert svc.run_query("SELECT count(*) AS n FROM rb_view")["rows"][0][0] == 3
    usage = svc.dataset_usage("rb_view")
    assert usage["view_exists"] is True and usage["physical_tables"] == ["rb_view__r1"]
    assert svc.dataset_usage("rb_view_new")["view_exists"] is False
    assert svc.dataset_usage("rb_view_new")["physical_tables"] == []


def test_rename_rolls_back_when_first_physical_rename_fails(vault_dir, monkeypatch):
    svc.ingest_dataframe("rb_first", _df())

    def boom(con, old_name, new_name):
        raise RuntimeError("注入失败：改物理表")

    with monkeypatch.context() as mp:
        mp.setattr(svc, "_rename_physical_table", boom)
        with pytest.raises(RuntimeError):
            svc.rename_dataset("rb_first", "rb_first_new")

    assert svc.run_query("SELECT count(*) AS n FROM rb_first")["rows"][0][0] == 3
    assert svc.dataset_usage("rb_first")["physical_tables"] == ["rb_first__r1"]
    assert svc.dataset_usage("rb_first_new")["physical_tables"] == []


def test_rename_rolls_back_when_metadata_update_fails(vault_dir, monkeypatch):
    svc.ingest_dataframe("rb_meta", _df())

    def boom(table, new_table):
        raise RuntimeError("注入失败：元数据写入")

    with monkeypatch.context() as mp:
        mp.setattr(svc, "_rename_metadata", boom)
        with pytest.raises(svc.DatasetConflict) as excinfo:
            svc.rename_dataset("rb_meta", "rb_meta_new")
    assert "回滚" in str(excinfo.value)

    # 旧名仍可查询，物理表/视图回到原名，新名无残留
    assert svc.run_query("SELECT count(*) AS n FROM rb_meta")["rows"][0][0] == 3
    usage = svc.dataset_usage("rb_meta")
    assert usage["view_exists"] is True and usage["physical_tables"] == ["rb_meta__r1"]
    assert svc.dataset_usage("rb_meta_new")["view_exists"] is False
    assert svc.dataset_usage("rb_meta_new")["physical_tables"] == []
    assert "rb_meta" in _table_names() and "rb_meta_new" not in _table_names()

    # 补偿后可再次重命名
    svc.rename_dataset("rb_meta", "rb_meta_ok")
    assert svc.run_query("SELECT count(*) AS n FROM rb_meta_ok")["rows"][0][0] == 3


def test_rename_rolls_back_after_partial_physical_renames(vault_dir, monkeypatch):
    """第二张物理表改名失败：已改名的要还原，视图要指回原「最新修订」（数据真相）。"""
    svc.ingest_dataframe("rb_part", _df())
    svc.ingest_dataframe("rb_part", _df().head(1), mode="replace")  # r2 最新：1 行
    real = svc._rename_physical_table
    calls = {"n": 0}

    def flaky(con, old_name, new_name):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("注入失败：第二张物理表")
        real(con, old_name, new_name)

    with monkeypatch.context() as mp:
        mp.setattr(svc, "_rename_physical_table", flaky)
        with pytest.raises(RuntimeError, match="第二张物理表"):
            svc.rename_dataset("rb_part", "rb_part_new")

    usage = svc.dataset_usage("rb_part")
    assert usage["physical_tables"] == ["rb_part__r1", "rb_part__r2"]
    assert usage["view_exists"] is True
    # 视图必须回到 r2（1 行），不能被错误地指向 r1（3 行）
    assert svc.run_query("SELECT count(*) AS n FROM rb_part")["rows"][0][0] == 1
    assert svc.dataset_usage("rb_part_new")["physical_tables"] == []


def test_rollback_collects_errors_without_masking(vault_dir, monkeypatch):
    """补偿自身失败时收集说明而不抛出新异常（主异常优先上抛）。"""
    svc.ingest_dataframe("rb_col", _df())
    with monkeypatch.context() as mp:
        mp.setattr(
            svc, "_rename_physical_table",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("还原失败")),
        )
        con = svc._connect()
        try:
            errors = svc._rollback_rename(
                con, "rb_col", "rb_col_new", [("rb_col__r1", "rb_col_new__r1")], "rb_col__r1",
            )
        finally:
            con.close()
    assert errors and any("还原物理表" in e for e in errors)
    # 视图仍由 old_latest 重建成功，旧名可查询
    assert svc.run_query("SELECT count(*) AS n FROM rb_col")["rows"][0][0] == 3


def test_rename_metadata_requires_registered_row(vault_dir):
    with pytest.raises(LookupError):
        svc._rename_metadata("no_such_dataset", "no_such_new")


# ---------------- 入仓登记与删除/重命名同锁（复核残余边界） ----------------

def test_ingest_registers_metadata_inside_write_lock(vault_dir, monkeypatch):
    """登记必须与视图/物理表写在同一写锁临界区，delete/rename 无法插进中间。"""
    real = svc._register_dataset
    seen: list[bool] = []

    def spy(table, source, rows, columns):
        acquired = svc._write_lock.acquire(blocking=False)
        if acquired:  # 能拿到锁说明登记跑在锁外（缺陷）
            svc._write_lock.release()
        seen.append(not acquired)
        return real(table, source, rows, columns)

    monkeypatch.setattr(svc, "_register_dataset", spy)
    svc.ingest_dataframe("lock_meta", _df())
    assert seen == [True]


def test_delete_cannot_interleave_between_ingest_and_registration(vault_dir, monkeypatch):
    """入仓登记被阻塞期间，删除必须被同一写锁挡住；放行后删除看到已登记数据集。"""
    entered, release = threading.Event(), threading.Event()
    real = svc._register_dataset

    def slow_register(table, source, rows, columns):
        entered.set()
        assert release.wait(10), "测试超时：登记未被放行"
        return real(table, source, rows, columns)

    monkeypatch.setattr(svc, "_register_dataset", slow_register)
    ingest_errors: list[BaseException] = []

    def run_ingest():
        try:
            svc.ingest_dataframe("race_src", _df())
        except BaseException as exc:  # 线程内异常带回主线程断言
            ingest_errors.append(exc)

    ingest_thread = threading.Thread(target=run_ingest, daemon=True)
    ingest_thread.start()
    assert entered.wait(10), "入仓未进入登记阶段"

    delete_started = threading.Event()
    real_validate = svc.validate_table_name

    def spy_validate(name):
        # delete_dataset 的第一步行（取锁之前）——据此确认删除线程已到锁门前
        delete_started.set()
        return real_validate(name)

    monkeypatch.setattr(svc, "validate_table_name", spy_validate)
    delete_done = threading.Event()
    delete_result: dict = {}
    delete_errors: list[BaseException] = []

    def run_delete():
        try:
            delete_result.update(svc.delete_dataset("race_src", "race_src"))
        except BaseException as exc:
            delete_errors.append(exc)
        finally:
            delete_done.set()

    delete_thread = threading.Thread(target=run_delete, daemon=True)
    delete_thread.start()
    try:
        assert delete_started.wait(10), "删除线程未进入 delete_dataset"
        # 删除已到锁门前且登记仍被阻塞：此刻删除不可能完成，否则登记跑在锁外
        assert not delete_done.wait(0.5), "删除先于登记完成：登记未与写入同锁"
    finally:
        release.set()
        ingest_thread.join(10)
        delete_thread.join(10)
    assert not ingest_errors, ingest_errors
    assert not delete_errors, delete_errors
    assert delete_result.get("deleted") is True
    assert delete_result.get("dropped_physical") == 1
    usage = svc.dataset_usage("race_src")
    assert usage["registered"] is False and usage["physical_tables"] == []


def _failing_register(*args, **kwargs):
    raise RuntimeError("登记失败")


def test_ingest_registration_failure_rolls_back_new_dataset(vault_dir, monkeypatch):
    with monkeypatch.context() as mp:
        mp.setattr(svc, "_register_dataset", _failing_register)
        with pytest.raises(RuntimeError, match="登记失败"):
            svc.ingest_dataframe("reg_new", _df())
    usage = svc.dataset_usage("reg_new")
    assert usage["registered"] is False and usage["relation_kind"] == ""
    assert usage["physical_tables"] == []


def test_ingest_registration_failure_restores_previous_revision(vault_dir, monkeypatch):
    svc.ingest_dataframe("reg_keep", _df())
    with monkeypatch.context() as mp:
        mp.setattr(svc, "_register_dataset", _failing_register)
        with pytest.raises(RuntimeError, match="登记失败"):
            svc.ingest_dataframe("reg_keep", _df().head(1), mode="replace")

    # 视图回到上一修订、新修订表已丢弃，登记元数据与数据仓仍一致
    assert svc.run_query("SELECT count(*) AS n FROM reg_keep")["rows"][0][0] == 3
    usage = svc.dataset_usage("reg_keep")
    assert usage["physical_tables"] == ["reg_keep__r1"] and usage["consistent"] is True
    row = next(t for t in svc.list_datasets() if t["table"] == "reg_keep")
    assert row["rows"] == 3


def test_ingest_registration_failure_after_append_keeps_rows(vault_dir, monkeypatch):
    svc.ingest_dataframe("reg_app", _df())
    with monkeypatch.context() as mp:
        mp.setattr(svc, "_register_dataset", _failing_register)
        with pytest.raises(RuntimeError, match="登记失败"):
            svc.ingest_dataframe(
                "reg_app", pd.DataFrame({"月份": ["2026-11"], "金额": [50.0]}), mode="append",
            )
    # 追加已写入既有物理表：保留数据（重试按整行去重），登记行数待下次成功入仓修正
    assert svc.run_query("SELECT count(*) AS n FROM reg_app")["rows"][0][0] == 4
    row = next(t for t in svc.list_datasets() if t["table"] == "reg_app")
    assert row["rows"] == 3


def test_ingest_view_creation_failure_discards_new_revision(vault_dir):
    """建视图失败（同名 BASE TABLE 占用）时丢弃本次新建修订表，不碰既有对象。"""
    with svc._write_lock:
        con = svc._connect()
        try:
            con.execute("CREATE TABLE clash_blocker AS SELECT 1 AS a")
        finally:
            con.close()

    with pytest.raises(ValueError, match="入仓失败"):
        svc.ingest_dataframe("clash_blocker", _df())
    usage = svc.dataset_usage("clash_blocker")
    assert usage["registered"] is False
    assert usage["physical_tables"] == []  # clash_blocker__r1 已清理
    assert usage["relation_kind"] == "BASE TABLE"  # 既有同名表不受影响


# ---------------- 清洗/聚合另存：目标检查与日志写入同锁（残余边界） ----------------

def test_clean_apply_concurrent_same_target_only_one_wins(vault_dir, monkeypatch):
    """两个清洗都通过锁外预判后，锁内重取必须只放行一个（不能双写同一目标）。"""
    svc.ingest_dataframe("cc_src", _df())
    barrier = threading.Barrier(2, timeout=10)
    real_run = cl._run_clean

    def gated_run(table, recipe):
        data, report = real_run(table, recipe)
        barrier.wait(timeout=10)  # 两个线程都已通过锁外「目标不存在」预判
        return data, report

    monkeypatch.setattr(cl, "_run_clean", gated_run)
    results: list[dict] = []
    errors: list[BaseException] = []

    def worker():
        try:
            results.append(cl.clean_apply("cc_src", None, target_table="cc_out"))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)

    assert len(results) == 1 and len(errors) == 1, (results, errors)
    assert isinstance(errors[0], svc.DatasetConflict)
    assert svc.run_query("SELECT count(*) AS n FROM cc_out")["rows"][0][0] == 3
    assert len(cl.get_clean_logs("cc_src")) == 1  # 只落一条清洗日志


def test_aggregate_save_concurrent_same_target_only_one_wins(vault_dir, monkeypatch):
    svc.ingest_dataframe("ca_src", _df())
    barrier = threading.Barrier(2, timeout=10)
    real_agg = cl.aggregate_query

    def gated_agg(table, spec):
        result = real_agg(table, spec)
        barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(cl, "aggregate_query", gated_agg)
    spec = {"metrics": [{"column": "*", "agg": "count"}]}
    results: list[dict] = []
    errors: list[BaseException] = []

    def worker():
        try:
            results.append(cl.save_aggregate_as_dataset("ca_src", spec, "ca_out"))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)

    assert len(results) == 1 and len(errors) == 1, (results, errors)
    assert isinstance(errors[0], svc.DatasetConflict)
    assert svc.run_query("SELECT * FROM ca_out")["rows"] == [[3]]


def test_clean_log_written_inside_write_lock(vault_dir, monkeypatch):
    """清洗日志必须与结果入仓同处写锁临界区（删除无法插在两者之间）。"""
    svc.ingest_dataframe("log_src", _df())
    real_session = cl.SessionLocal
    seen: list[bool] = []

    def spy_session(*args, **kwargs):
        acquired = svc._write_lock.acquire(blocking=False)
        if acquired:  # 能拿到锁说明日志写在锁外（缺陷）
            svc._write_lock.release()
        seen.append(not acquired)
        return real_session(*args, **kwargs)

    monkeypatch.setattr(cl, "SessionLocal", spy_session)
    cl.clean_apply("log_src", None, target_table="log_out")
    assert seen == [True]


def test_delete_waits_for_clean_log_write(vault_dir, monkeypatch):
    """日志写入被阻塞期间，删除必须被写锁挡住，绝不产生指向已删目标的日志。"""
    svc.ingest_dataframe("race_clean", _df())
    entered, release = threading.Event(), threading.Event()
    real_session = cl.SessionLocal

    @contextlib.contextmanager
    def gated_session():
        entered.set()
        assert release.wait(10), "测试超时：日志写入未被放行"
        with real_session() as db:
            yield db

    monkeypatch.setattr(cl, "SessionLocal", gated_session)

    clean_errors: list[BaseException] = []

    def run_clean():
        try:
            cl.clean_apply("race_clean", None, target_table="race_clean_out")
        except BaseException as exc:
            clean_errors.append(exc)

    clean_thread = threading.Thread(target=run_clean, daemon=True)
    clean_thread.start()
    assert entered.wait(10), "清洗未进入日志写入阶段"

    delete_done = threading.Event()
    delete_errors: list[BaseException] = []

    def run_delete():
        try:
            svc.delete_dataset("race_clean_out", "race_clean_out")
        except BaseException as exc:
            delete_errors.append(exc)
        finally:
            delete_done.set()

    delete_thread = threading.Thread(target=run_delete, daemon=True)
    delete_thread.start()
    try:
        assert not delete_done.wait(0.5), "删除在日志写入前完成：日志会指向已删目标"
    finally:
        release.set()
        clean_thread.join(10)
        delete_thread.join(10)

    assert not clean_errors, clean_errors
    # 日志已落库，删除因此被引用拦截（需要 force 才可删）
    assert isinstance(delete_errors[0], svc.DatasetConflict)
    assert svc.dataset_usage("race_clean_out")["references"]["clean_logs"] == 1
    assert cl.get_clean_logs("race_clean")[0]["result_table"] == "race_clean_out"


def test_clean_apply_aborts_when_source_deleted_before_write(vault_dir, monkeypatch):
    """源数据集在清洗计算后被删除：拒绝落结果与日志，不留悬空引用。"""
    svc.ingest_dataframe("gone_src", _df())
    real_run = cl._run_clean

    def run_then_delete(table, recipe):
        data, report = real_run(table, recipe)
        svc.delete_dataset(table, table)  # 锁外窗口内删除源数据集
        return data, report

    monkeypatch.setattr(cl, "_run_clean", run_then_delete)
    with pytest.raises(svc.DatasetConflict, match="源数据集已被删除"):
        cl.clean_apply("gone_src", None, target_table="gone_out")

    assert "gone_out" not in _table_names()
    assert svc.dataset_usage("gone_out")["physical_tables"] == []


def test_save_aggregate_aborts_when_source_deleted_before_write(vault_dir, monkeypatch):
    svc.ingest_dataframe("gone_agg", _df())
    real_agg = cl.aggregate_query

    def agg_then_delete(table, spec):
        result = real_agg(table, spec)
        svc.delete_dataset(table, table)
        return result

    monkeypatch.setattr(cl, "aggregate_query", agg_then_delete)
    with pytest.raises(svc.DatasetConflict, match="源数据集已被删除"):
        cl.save_aggregate_as_dataset(
            "gone_agg", {"metrics": [{"column": "*", "agg": "count"}]}, "gone_agg_out")
    assert "gone_agg_out" not in _table_names()


# ---------------- HTTP 契约（前端 T6 依赖） ----------------

def test_http_usage_and_delete_flow(client, vault_dir):
    svc.ingest_dataframe("http_src", _df())
    cl.clean_apply("http_src", None, target_table="http_out")

    usage = client.get("/api/finance/vault/tables/http_src/usage")
    assert usage.status_code == 200
    body = usage.json()
    assert body["requires_force"] is True and body["confirm_name"] == "http_src"
    assert body["references"]["clean_logs"] == 1
    assert body["physical_tables"] == ["http_src__r1"]

    missing = client.delete("/api/finance/vault/tables/http_src")
    assert missing.status_code == 422  # confirm 为必填二次确认参数

    wrong = client.delete("/api/finance/vault/tables/http_src", params={"confirm": "other"})
    assert wrong.status_code == 400 and "确认" in wrong.json()["detail"]

    blocked = client.delete("/api/finance/vault/tables/http_src", params={"confirm": "http_src"})
    assert blocked.status_code == 409 and "引用" in blocked.json()["detail"]

    ok = client.delete(
        "/api/finance/vault/tables/http_src",
        params={"confirm": "http_src", "force": "true"},
    )
    assert ok.status_code == 200 and ok.json()["deleted"] is True
    assert client.get("/api/finance/vault/tables/http_src/preview").status_code == 404

    gone = client.delete(
        "/api/finance/vault/tables/http_src",
        params={"confirm": "http_src", "force": "true"},
    )
    assert gone.status_code == 404

    bad_name = client.delete("/api/finance/vault/tables/Bad-Name", params={"confirm": "Bad-Name"})
    assert bad_name.status_code == 400


def test_http_long_name_lifecycle(client, vault_dir):
    """64 字符数据集经 HTTP 走完 ingest → usage → delete（物理名 68 字符）。"""
    name = "z" * 64
    svc.ingest_dataframe(name, _df())

    usage = client.get(f"/api/finance/vault/tables/{name}/usage")
    assert usage.status_code == 200
    assert usage.json()["physical_tables"] == [name + "__r1"]

    deleted = client.delete(f"/api/finance/vault/tables/{name}", params={"confirm": name})
    assert deleted.status_code == 200 and deleted.json()["dropped_physical"] == 1
    assert client.get(f"/api/finance/vault/tables/{name}/preview").status_code == 404


def test_http_aggregate_save_conflict_and_rename(client, vault_dir):
    svc.ingest_dataframe("hagg", _df())
    payload = {**_aggregate_spec(), "target_table": "hagg_out"}
    first = client.post("/api/finance/vault/tables/hagg/aggregate/save", json=payload)
    assert first.status_code == 200 and first.json()["result_table"] == "hagg_out"

    dup = client.post("/api/finance/vault/tables/hagg/aggregate/save", json=payload)
    assert dup.status_code == 409 and "已存在" in dup.json()["detail"]

    over = client.post(
        "/api/finance/vault/tables/hagg/aggregate/save",
        json={**payload, "allow_overwrite": True},
    )
    assert over.status_code == 200 and over.json()["result_table"] == "hagg_out"

    # 清洗应用同口径：已存在结果表 409，显式覆盖才通过
    clean_payload = {"target_table": "hagg_clean"}
    assert client.post("/api/finance/vault/tables/hagg/clean/apply", json=clean_payload).status_code == 200
    dup_clean = client.post("/api/finance/vault/tables/hagg/clean/apply", json=clean_payload)
    assert dup_clean.status_code == 409
    over_clean = client.post(
        "/api/finance/vault/tables/hagg/clean/apply",
        json={**clean_payload, "allow_overwrite": True},
    )
    assert over_clean.status_code == 200

    ren = client.post("/api/finance/vault/tables/hagg/rename", json={"new_table": "hagg_new"})
    assert ren.status_code == 200 and ren.json()["table"] == "hagg_new"
    conflict = client.post(
        "/api/finance/vault/tables/hagg_new/rename", json={"new_table": "hagg_out"})
    assert conflict.status_code == 409
    invalid = client.post(
        "/api/finance/vault/tables/hagg_new/rename", json={"new_table": "Bad Name"})
    assert invalid.status_code == 400
    missing = client.post(
        "/api/finance/vault/tables/hagg_missing/rename", json={"new_table": "hagg_x"})
    assert missing.status_code == 404
    assert client.get("/api/finance/vault/tables/hagg_new/preview").status_code == 200
