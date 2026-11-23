# -*- coding: utf-8 -*-
"""finance_recon 模块测试（Task 1 + Task 2）：模块骨架、六表模型、批次 API 合约、
状态机，以及四类源文件上传/删除/事实记录查询端点。

范围与计划对齐：
- 六张 ``mod_recon_*`` 表及其唯一约束（term 批次×合同 / source 批次×SHA-256 /
  record 来源行 / match 批次×认领×核销）在 create_all 后真实生效；
- Task 1 批次五类端点（POST / GET 列表 / GET 详情 / PATCH / DELETE）；
- Task 2 端点：POST sources/{kind}（重复内容 200 + skipped_duplicates）、
  DELETE sources/{source_id}（人工引用 409、仅 draft/failed）、
  GET records（page/page_size≤200 + kind/合同/单据过滤，未填金额为 null）；
- Task 3 端点：POST batches/{id}/check（precheck 400 / 引擎失败 failed 可恢复 /
  成功 ready）、GET/PUT terms（ready 修改置 needs_recheck、导出守卫 409）、
  GET/POST/PATCH matches（唯一三元组、确认超额 409）、GET/POST/PATCH issues
  （人工确认/排除需说明）、GET batches/{id}/summary 与 recon_summary 工具扩展；
  exports 由 Task 4 实现，不留 501 占位；
- 状态机 draft→checking→ready|failed、failed→checking、ready→checking；
- 空 contract_scope 不允许进入 checking（check 前必须显式勾选本批合同）；
- 唯一约束 IntegrityError 由服务层守卫映射中文 409（ReconConflictError → HTTP 409）；
- 只读 Agent summary 工具 ``recon_summary`` 按批次返回状态、计数、合计与待确认数。

测试一律临时 SQLite + 合成数据（conftest 隔离），不依赖真实目录与真实合同号。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

P = "/api/finance/recon"

# 合成合同号（SYN 前缀，避免与真实合同撞名）
SCOPE = ["HT-SYN-001", "HT-SYN-002"]
CREATE_BODY = {
    "name": "11月商户核销对账",
    "object_tag": "示例商户",
    "as_of": "2026-11-18",
    "contract_scope": SCOPE,
}


@pytest.fixture(autouse=True)
def _ensure_tables():
    """非 client 直触库的用例保证 recon 六表存在（契约：test_test_isolation_contract）。"""
    from sqlalchemy import inspect

    from app.database import Base, engine

    if not inspect(engine).has_table("mod_recon_batch"):
        Base.metadata.create_all(bind=engine)
    yield


# ---------- 模块注册与工具 schema ----------


def test_manifest_loaded_with_validated_contract():
    """manifest 加载成功且校验通过：校验失败会被注册器静默跳过，必须显式断言。"""
    from app.modules import get_loaded_modules
    from app.tool_schema import params_errors

    loaded = {m.name: m for m in get_loaded_modules()}
    assert "finance_recon" in loaded
    manifest = loaded["finance_recon"]
    assert manifest.title == "商户核销对账"
    assert manifest.prefix == "/api/finance/recon"
    assert manifest.validate() == []
    tools = manifest.agent_tools
    assert len(tools) == 1
    tool = tools[0]
    assert tool["name"] == "recon_summary"
    assert callable(tool["func"])
    assert tool["readonly"] is True
    assert tool["params"] == {} and tool["required"] == []
    assert params_errors("recon_summary", tool["params"], tool["required"]) == []


def test_summary_tool_registered_into_tool_specs(client):
    from app.agents.tools import TOOL_SPECS

    assert "recon_summary" in TOOL_SPECS
    spec = TOOL_SPECS["recon_summary"]
    assert spec["readonly"] is True and callable(spec["func"])
    assert client.post(f"{P}/batches", json=CREATE_BODY).status_code == 201
    assert spec["func"]()["batches"] >= 1  # 工具真实可查且能读到批次


def test_six_tables_registered_and_source_kinds_locked():
    from app.database import Base

    tables = set(Base.metadata.tables)
    for table in (
        "mod_recon_batch",
        "mod_recon_source",
        "mod_recon_record",
        "mod_recon_match",
        "mod_recon_issue",
        "mod_recon_term",
    ):
        assert table in tables, f"缺少模块表 {table}"

    from app.modules.finance_recon.schemas import SOURCE_KINDS, SOURCE_KIND_LABELS

    # 四类 kind 唯一事实源（计划 77-78 行）：核销/认领/欠费/账单
    assert SOURCE_KINDS == ("writeoff", "claim", "arrear", "bill")
    assert SOURCE_KIND_LABELS == {
        "writeoff": "核销",
        "claim": "认领",
        "arrear": "欠费",
        "bill": "账单",
    }


def test_unique_constraints_fire_at_db_level():
    """四组唯一约束在真实库点火：term 批次×合同 / source 批次×SHA / record 来源行 / match 三元组。"""
    from app.database import SessionLocal
    from app.modules.finance_recon.models import (
        ReconBatch,
        ReconMatch,
        ReconRecord,
        ReconSource,
        ReconTerm,
    )

    with SessionLocal() as db:
        batch = ReconBatch(
            name="约束点火批次",
            as_of=date(2026, 11, 18),
            contract_scope=list(SCOPE),
            status="draft",
        )
        db.add(batch)
        db.flush()
        source = ReconSource(
            batch_id=batch.id, kind="writeoff", original_name="核销明细.xlsx", sha256="a" * 64
        )
        db.add(source)
        db.flush()
        claim = ReconRecord(batch_id=batch.id, source_id=source.id, row_no=1, kind="claim")
        writeoff = ReconRecord(batch_id=batch.id, source_id=source.id, row_no=2, kind="writeoff")
        db.add_all([claim, writeoff])
        db.flush()

        db.add(ReconTerm(batch_id=batch.id, contract_no="HT-SYN-001", terms={}))
        db.commit()
        db.add(ReconTerm(batch_id=batch.id, contract_no="HT-SYN-001", terms={}))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        db.add(
            ReconSource(batch_id=batch.id, kind="claim", original_name="副本.xlsx", sha256="a" * 64)
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        db.add(ReconRecord(batch_id=batch.id, source_id=source.id, row_no=1, kind="writeoff"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        db.add(
            ReconMatch(
                batch_id=batch.id,
                claim_record_id=claim.id,
                writeoff_record_id=writeoff.id,
                allocated_amount=Decimal("1.00"),
            )
        )
        db.commit()
        db.add(
            ReconMatch(
                batch_id=batch.id,
                claim_record_id=claim.id,
                writeoff_record_id=writeoff.id,
                allocated_amount=Decimal("2.00"),
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


# ---------- 批次 API：创建 / 列表 / 详情 ----------


def test_batch_create_list_detail(client):
    r = client.post(f"{P}/batches", json=CREATE_BODY)
    assert r.status_code == 201, r.text
    body = r.json()
    bid = body["id"]
    assert body["status"] == "draft"
    assert body["status_label"] == "草稿"
    assert body["as_of"] == "2026-11-18"
    assert body["tolerance"] == "0.01"  # 容差显式记录并两位小数序列化
    assert body["contract_scope"] == SCOPE
    assert body["needs_recheck"] is False

    second = client.post(f"{P}/batches", json={**CREATE_BODY, "name": "第二批次"})
    assert second.status_code == 201

    lst = client.get(f"{P}/batches")
    assert lst.status_code == 200
    data = lst.json()
    assert data["total"] == 2
    assert [item["name"] for item in data["items"]] == ["第二批次", "11月商户核销对账"]
    # 列表为精简视图：不含合同范围与子表计数
    assert "contract_scope" not in data["items"][0]
    assert "counts" not in data["items"][0]

    det = client.get(f"{P}/batches/{bid}")
    assert det.status_code == 200
    detail = det.json()
    assert detail["contract_scope"] == SCOPE
    assert detail["counts"] == {"sources": 0, "records": 0, "matches": 0, "issues": 0, "terms": 0}

    missing = client.get(f"{P}/batches/999999")
    assert missing.status_code == 404
    assert "999999" in missing.json()["detail"]


def test_batch_create_validation_rejects_illegal_values(client):
    # 对账日：显式必填、严格 YYYY-MM-DD，不允许默认到当天
    without_as_of = {k: v for k, v in CREATE_BODY.items() if k != "as_of"}
    assert client.post(f"{P}/batches", json=without_as_of).status_code == 422
    assert client.post(f"{P}/batches", json={**CREATE_BODY, "as_of": "2026-13-01"}).status_code == 422
    assert client.post(f"{P}/batches", json={**CREATE_BODY, "as_of": "2026/11/18"}).status_code == 422
    # 容差：负数、三位小数拒绝
    assert client.post(f"{P}/batches", json={**CREATE_BODY, "tolerance": "-0.01"}).status_code == 422
    assert client.post(f"{P}/batches", json={**CREATE_BODY, "tolerance": "0.005"}).status_code == 422
    # 名称空白拒绝；对象标签只做修剪
    assert client.post(f"{P}/batches", json={**CREATE_BODY, "name": "   "}).status_code == 422
    # 合同范围：空白项拒绝；空列表允许（draft 可先建批，check 前必须勾选）
    assert (
        client.post(
            f"{P}/batches", json={**CREATE_BODY, "contract_scope": ["HT-SYN-001", "   "]}
        ).status_code
        == 422
    )
    ok = client.post(f"{P}/batches", json={**CREATE_BODY, "contract_scope": []})
    assert ok.status_code == 201 and ok.json()["contract_scope"] == []


def test_contract_scope_normalized_on_create(client):
    """范围项修剪空白、保序去重；范围大小受 200 项上限约束。"""
    r = client.post(
        f"{P}/batches",
        json={**CREATE_BODY, "contract_scope": [" HT-SYN-002 ", "HT-SYN-001", "HT-SYN-002"]},
    )
    assert r.status_code == 201, r.text
    assert r.json()["contract_scope"] == ["HT-SYN-002", "HT-SYN-001"]
    too_many = client.post(
        f"{P}/batches", json={**CREATE_BODY, "contract_scope": [f"HT-SYN-{i:03d}" for i in range(201)]}
    )
    assert too_many.status_code == 422


def test_conflict_error_maps_to_409(client, monkeypatch):
    """服务层 ReconConflictError（含 IntegrityError 守卫产物）统一映射中文 409。"""
    from app.modules.finance_recon import service

    def boom(db, payload):
        raise service.ReconConflictError("批次创建：唯一约束冲突，请检查是否重复提交")

    monkeypatch.setattr(service, "create_batch", boom)
    r = client.post(f"{P}/batches", json=CREATE_BODY)
    assert r.status_code == 409
    assert "唯一约束冲突" in r.json()["detail"]


def test_unique_guard_translates_integrity_error(client):
    """真实唯一约束 + 服务层守卫：重复 (batch_id, contract_no) 提交 → ReconConflictError 中文 409。"""
    from app.database import SessionLocal
    from app.modules.finance_recon import service
    from app.modules.finance_recon.models import ReconTerm

    bid = client.post(f"{P}/batches", json=CREATE_BODY).json()["id"]
    with SessionLocal() as db:
        with pytest.raises(service.ReconConflictError) as exc:
            with service.unique_guard(db, "人工口径参数"):
                db.add(ReconTerm(batch_id=bid, contract_no="HT-SYN-001", terms={}))
                db.add(ReconTerm(batch_id=bid, contract_no="HT-SYN-001", terms={}))
                db.commit()
        assert "人工口径参数" in str(exc.value)
        assert "唯一约束冲突" in str(exc.value)
        db.rollback()


# ---------- 批次 PATCH / DELETE 与状态守卫 ----------


def _transition(batch_id: int, target: str) -> dict:
    """Task 1 没有 check 端点：状态迁移走共享入口 service.transition_batch。"""
    from app.database import SessionLocal
    from app.modules.finance_recon import service

    with SessionLocal() as db:
        return service.transition_batch(db, batch_id, target)


def test_patch_rules_and_validation(client):
    bid = client.post(f"{P}/batches", json=CREATE_BODY).json()["id"]
    p = client.patch(
        f"{P}/batches/{bid}",
        json={"name": "改名后的批次", "as_of": "2026-11-30", "tolerance": "0.02", "contract_scope": ["HT-SYN-003"]},
    )
    assert p.status_code == 200, p.text
    assert p.json()["name"] == "改名后的批次"
    assert p.json()["as_of"] == "2026-11-30"
    assert p.json()["tolerance"] == "0.02"
    assert p.json()["contract_scope"] == ["HT-SYN-003"]

    assert client.patch(f"{P}/batches/{bid}", json={}).status_code == 400
    assert client.patch(f"{P}/batches/{bid}", json={"tolerance": "1.234"}).status_code == 422
    assert client.patch(f"{P}/batches/{bid}", json={"as_of": "2026/11/30"}).status_code == 422
    assert client.patch(f"{P}/batches/{bid}", json={"name": "   "}).status_code == 422
    assert client.patch(f"{P}/batches/424242", json={"name": "x"}).status_code == 404


def test_ready_batch_patch_and_delete_conflict_409(client):
    bid = client.post(f"{P}/batches", json=CREATE_BODY).json()["id"]
    _transition(bid, "checking")
    _transition(bid, "ready")

    patch = client.patch(f"{P}/batches/{bid}", json={"name": "不应成功"})
    assert patch.status_code == 409
    assert "审计留痕" in patch.json()["detail"]  # P2-1：ever_ready 守卫优先于状态文案
    delete = client.delete(f"{P}/batches/{bid}")
    assert delete.status_code == 409
    assert "审计留痕" in delete.json()["detail"]

    # checking 状态同样禁改禁删（仅 draft/failed 可维护）
    _transition(bid, "checking")
    assert client.patch(f"{P}/batches/{bid}", json={"name": "x"}).status_code == 409
    assert client.delete(f"{P}/batches/{bid}").status_code == 409


def test_failed_and_draft_deletable(client):
    first = client.post(f"{P}/batches", json=CREATE_BODY).json()["id"]
    _transition(first, "checking")
    _transition(first, "failed")
    assert client.delete(f"{P}/batches/{first}").status_code == 200
    assert client.get(f"{P}/batches/{first}").status_code == 404

    second = client.post(f"{P}/batches", json={**CREATE_BODY, "name": "草稿直删"}).json()["id"]
    assert client.delete(f"{P}/batches/{second}").status_code == 200
    assert client.get(f"{P}/batches/{second}").status_code == 404

    lst = client.get(f"{P}/batches")
    assert lst.json()["total"] == 0


def test_batch_delete_cascades_children(client):
    bid = client.post(f"{P}/batches", json=CREATE_BODY).json()["id"]
    from app.database import SessionLocal
    from app.modules.finance_recon.models import (
        ReconBatch,
        ReconIssue,
        ReconMatch,
        ReconRecord,
        ReconSource,
        ReconTerm,
    )

    with SessionLocal() as db:
        batch = db.get(ReconBatch, bid)
        source = ReconSource(batch_id=bid, kind="bill", original_name="账单.xlsx", sha256="b" * 64)
        db.add(source)
        db.flush()
        record = ReconRecord(batch_id=bid, source_id=source.id, row_no=1, kind="bill")
        db.add(record)
        db.flush()
        db.add_all(
            [
                ReconTerm(batch_id=bid, contract_no="HT-SYN-001", terms={}),
                ReconIssue(batch_id=bid, origin="machine", fingerprint="fp-1", category="口径"),
                ReconMatch(
                    batch_id=bid,
                    claim_record_id=record.id,
                    writeoff_record_id=record.id,
                    allocated_amount=Decimal("0.00"),
                ),
            ]
        )
        db.commit()
        assert batch is not None

    assert client.delete(f"{P}/batches/{bid}").status_code == 200
    with SessionLocal() as db:
        assert db.get(ReconBatch, bid) is None
        for model in (ReconSource, ReconRecord, ReconMatch, ReconIssue, ReconTerm):
            assert db.query(model).filter(model.batch_id == bid).count() == 0


def test_batch_delete_removes_upload_dir(client, tmp_path, monkeypatch):
    """删批次必须同时移除 UPLOAD_DIR/recon/<batch_id>/，不留孤儿目录。"""
    from app import config as app_config

    monkeypatch.setattr(app_config, "UPLOAD_DIR", tmp_path / "uploads")
    bid = client.post(f"{P}/batches", json=CREATE_BODY).json()["id"]
    batch_dir = tmp_path / "uploads" / "recon" / str(bid)
    (batch_dir / "nested").mkdir(parents=True)
    (batch_dir / "nested" / "账单样本.xlsx").write_text("synthetic", encoding="utf-8")

    assert client.delete(f"{P}/batches/{bid}").status_code == 200
    assert not batch_dir.exists()

    # 无上传目录的批次删除不受影响（目录不存在跳过，不是错误）
    other = client.post(f"{P}/batches", json={**CREATE_BODY, "name": "无目录批次"}).json()["id"]
    assert client.delete(f"{P}/batches/{other}").status_code == 200


def test_batch_upload_dir_fixed_layout(tmp_path, monkeypatch):
    """目录固定 UPLOAD_DIR/recon/<batch_id>/：resolved 后父目录与名称必须吻合，防越界。"""
    from app import config as app_config
    from app.modules.finance_recon import service

    monkeypatch.setattr(app_config, "UPLOAD_DIR", tmp_path / "uploads")
    expected = (tmp_path / "uploads" / "recon" / "42").resolve()
    assert service._batch_upload_dir(42) == expected


def test_batch_delete_cleanup_failure_rolls_back(client, tmp_path, monkeypatch):
    """目录清理失败：DB 回滚、批次保持原状可重试，返回中文 409（不静默吞错）。"""
    from app import config as app_config
    from app.modules.finance_recon import service

    monkeypatch.setattr(app_config, "UPLOAD_DIR", tmp_path / "uploads")
    bid = client.post(f"{P}/batches", json=CREATE_BODY).json()["id"]
    batch_dir = tmp_path / "uploads" / "recon" / str(bid)
    batch_dir.mkdir(parents=True)
    (batch_dir / "bill.xlsx").write_text("synthetic", encoding="utf-8")

    def boom(path, *args, **kwargs):
        raise OSError("synthetic cleanup failure")

    monkeypatch.setattr(service.shutil, "rmtree", boom)
    r = client.delete(f"{P}/batches/{bid}")
    assert r.status_code == 409
    assert "清理失败" in r.json()["detail"]
    detail = client.get(f"{P}/batches/{bid}")
    assert detail.status_code == 200 and detail.json()["status"] == "draft"
    assert batch_dir.exists()  # 清理失败时目录保留原状，等重试


# ---------- 状态机 ----------


def test_transition_matrix_rejects_illegal_moves(client):
    from app.modules.finance_recon import service

    bid = client.post(f"{P}/batches", json=CREATE_BODY).json()["id"]
    # draft 只能 → checking；不允许 draft→ready / draft→failed
    for target in ("ready", "failed"):
        with pytest.raises(service.ReconConflictError) as exc:
            _transition(bid, target)
        assert "不允许" in str(exc.value)
    # 未知目标状态 → 409 冲突
    with pytest.raises(service.ReconConflictError) as exc:
        _transition(bid, "bogus")
    assert "未知目标状态" in str(exc.value)
    assert client.get(f"{P}/batches/{bid}").json()["status"] == "draft"

    # 合法全路径：draft→checking→failed→checking→ready→checking
    assert _transition(bid, "checking")["status"] == "checking"
    assert _transition(bid, "failed")["status"] == "failed"
    assert _transition(bid, "checking")["status"] == "checking"
    assert _transition(bid, "ready")["status"] == "ready"
    assert _transition(bid, "checking")["status"] == "checking"


def test_transition_missing_batch_conflict_or_not_found():
    from app.modules.finance_recon import service

    with pytest.raises(service.ReconNotFoundError):
        _transition(987654, "checking")


def test_empty_scope_blocks_checking(client):
    """空 scope 不允许 check：进入 checking 前必须显式勾选本批合同。"""
    from app.modules.finance_recon import service

    bid = client.post(f"{P}/batches", json={**CREATE_BODY, "contract_scope": []}).json()["id"]
    with pytest.raises(ValueError) as exc:
        _transition(bid, "checking")
    assert "合同" in str(exc.value)
    assert client.get(f"{P}/batches/{bid}").json()["status"] == "draft"

    # 勾选后放行
    p = client.patch(f"{P}/batches/{bid}", json={"contract_scope": ["HT-SYN-001"]})
    assert p.status_code == 200
    assert _transition(bid, "checking")["status"] == "checking"


def test_transition_into_checking_clears_old_error_summary(client):
    """failed 重试进 checking 时清掉上一轮失败摘要，不残留旧错。"""
    bid = client.post(f"{P}/batches", json=CREATE_BODY).json()["id"]
    assert _transition(bid, "checking")["status"] == "checking"
    assert _transition(bid, "failed")["status"] == "failed"

    from app.database import SessionLocal
    from app.modules.finance_recon.models import ReconBatch

    with SessionLocal() as db:
        batch = db.get(ReconBatch, bid)
        batch.error_summary = "上一轮检查失败：账单缺文本层"
        db.commit()
    assert client.get(f"{P}/batches/{bid}").json()["error_summary"] != ""

    out = _transition(bid, "checking")
    assert out["error_summary"] == ""


# ---------- Task 2：四类源文件上传 / 删除 / 记录查询 ----------

_WO_HEADER = [
    "序号", "区域", "项目", "合同号", "合同类型", "账单月", "品牌", "铺位",
    "应收(账单)", "核销(账单)", "单据号", "核销日期", "取消日期", "核销类型",
    "费用类型", "费用期", "应收(费用期)", "核销(费用期)", "核销后欠费",
    "税率", "预收类别", "预收款金额",
]
_RC_HEADER = [
    "来源", "单据类型", "父单据", "子单据", "传输id", "业务类型", "付款户名", "付款账号",
    "收款户名", "收款账号", "收款金额", "本次认领", "手续费", "交易类型", "用途",
    "交易时间", "摘要", "状态", "认领时间", "认领人", "审核时间", "审核人",
]
_QF_HEADER = ["序号", "合同", "账单月", "缴费截止", "费用开始", "费用结束", "合计", "固定租金", "综管费"]
_BILL_LINES = [
    "付款通知单", "账单编号：ZD-SYN-202610-01", "合同编号：HT-SYN-001", "账单月：2026-10",
    "出账日期：2026-11-01", "缴费截止：2026-11-15", "累计应付", "1,234.56",
]


def _wo_row(*, seq=1, contract="HT-SYN-001", doc="DZ-SYN-0001", ar_fee="100.00"):
    return [
        seq, "华东", "项目甲", contract, "固定租金", "2026-10", "品牌甲", "铺位甲",
        "100.00", "100.00", doc, None, None, "正常核销", "固定租金",
        "2026.10.01-2026.10.31", ar_fee, "100.00", "0.00", "0.05", None, None,
    ]


def _rc_row(*, doc="SK-SYN-0001", recv="500.00", claim="500.00", fee=None):
    return [
        "线下", "收款", doc, "", "T-SYN-1", "收款", "付款方甲", "6222000011112222",
        "收款方乙", "110002003456", recv, claim, fee, "转账", "货款",
        "2026-11-06 10:00:00", "摘要甲", "已审核", "2026-11-06 11:00:00", "认领人甲",
        "2026-11-06 12:00:00", "审核人甲",
    ]


def _qf_row(*, seq=1, contract="HT-SYN-001"):
    return [seq, contract, "2026-10", "2026-11-15", "2026-10-01", "2026-10-31", "300.00", "200.00", "100.00"]


def _xlsx_bytes(header, rows, *, sheet="数据"):
    import io

    import openpyxl

    bio = io.BytesIO()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    ws.append(header)
    for row in rows:
        ws.append(list(row))
    wb.save(bio)
    wb.close()
    return bio.getvalue()


def _pdf_bytes(lines):
    import io

    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    y = 72
    for line in lines:
        page.insert_text((72, y), line, fontname="china-s", fontsize=11)
        y += 18
    bio = io.BytesIO()
    doc.save(bio)
    doc.close()
    return bio.getvalue()


def _upload(client, bid: int, kind: str, name: str, data: bytes):
    return client.post(f"{P}/batches/{bid}/sources/{kind}", files={"file": (name, data)})


def _seed_sources(client, bid: int) -> dict[str, int]:
    """四类各传一份合成文件，返回 kind → source_id。"""
    payload = {
        "writeoff": ("核销明细样本.xlsx", _xlsx_bytes(_WO_HEADER, [_wo_row(seq=1), _wo_row(seq=2, contract="HT-SYN-002", doc="DZ-SYN-0002")])),
        "claim": ("认领记录样本.xlsx", _xlsx_bytes(_RC_HEADER, [_rc_row(), _rc_row(doc="SK-SYN-0002", recv=200.0, claim=150.0, fee="2.00")])),
        "arrear": ("欠费明细样本.xlsx", _xlsx_bytes(_QF_HEADER, [_qf_row(), _qf_row(seq=2, contract="HT-SYN-002")])),
        "bill": ("账单通知单样本.pdf", _pdf_bytes(_BILL_LINES)),
    }
    ids: dict[str, int] = {}
    for kind, (name, blob) in payload.items():
        r = _upload(client, bid, kind, name, blob)
        assert r.status_code == 201, r.text
        ids[kind] = r.json()["source_id"]
    return ids


def test_upload_source_end_to_end_and_layout(client, tmp_path, monkeypatch):
    from app import config as app_config

    monkeypatch.setattr(app_config, "UPLOAD_DIR", tmp_path / "uploads")
    bid = client.post(f"{P}/batches", json=CREATE_BODY).json()["id"]
    r = _upload(client, bid, "writeoff", "核销明细样本.xlsx", _xlsx_bytes(_WO_HEADER, [_wo_row()]))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "writeoff" and body["kind_label"] == "核销"
    assert body["row_count"] == 1 and body["contract_count"] == 1
    assert body["amount_totals"]["amount_1"] == "100.00"
    assert body["skipped_duplicates"] == 0
    stored = tmp_path / "uploads" / "recon" / str(bid)
    assert stored.is_dir() and len(list(stored.iterdir())) == 1

    recs = client.get(f"{P}/batches/{bid}/records")
    assert recs.status_code == 200
    data = recs.json()
    assert data["total"] == 1
    item = data["items"][0]
    assert item["kind"] == "writeoff" and item["kind_label"] == "核销"
    assert item["contract_no"] == "HT-SYN-001" and item["doc_no"] == "DZ-SYN-0001"
    assert item["amount_1"] == "100.00" and item["amount_4"] is None  # 未填是 null，不是 0
    assert item["fee_period_start"] == "2026-10-01" and item["fee_period_end"] == "2026-10-31"
    assert item["source_name"] == "核销明细样本.xlsx"


def test_upload_source_guards_and_failure_summary(client):
    bid = client.post(f"{P}/batches", json=CREATE_BODY).json()["id"]
    # 非法 kind → 422（路径参数 Literal 校验）
    assert client.post(f"{P}/batches/{bid}/sources/magic", files={"file": ("a.xlsx", b"x")}).status_code == 422
    # 未知批次 → 404
    missing = _upload(client, 777777, "writeoff", "a.xlsx", _xlsx_bytes(_WO_HEADER, [_wo_row()]))
    assert missing.status_code == 404
    # 非法金额文本 → 400，且批次摘要保留可诊断信息、批次保持 draft
    bad = _upload(client, bid, "writeoff", "坏样本.xlsx", _xlsx_bytes(_WO_HEADER, [_wo_row(ar_fee="12.3元")]))
    assert bad.status_code == 400
    assert "12.3元" in bad.json()["detail"]
    detail = client.get(f"{P}/batches/{bid}").json()
    assert detail["status"] == "draft" and "坏样本.xlsx" in detail["error_summary"]
    assert detail["counts"]["records"] == 0
    # 删除失败源后摘要清空（draft 且无 failed 源）
    failed = client.get(f"{P}/batches/{bid}").json()["counts"]["sources"]
    assert failed == 1


def test_upload_duplicate_returns_skipped_duplicates(client):
    bid = client.post(f"{P}/batches", json=CREATE_BODY).json()["id"]
    data = _xlsx_bytes(_WO_HEADER, [_wo_row()])
    first = _upload(client, bid, "writeoff", "核销明细样本.xlsx", data)
    assert first.status_code == 201 and first.json()["skipped_duplicates"] == 0
    second = _upload(client, bid, "writeoff", "换名字再传.xlsx", data)
    assert second.status_code == 200
    body = second.json()
    assert body["skipped_duplicates"] == 1
    assert body["source_id"] == first.json()["source_id"]
    assert client.get(f"{P}/batches/{bid}").json()["counts"]["sources"] == 1
    assert client.get(f"{P}/batches/{bid}/records").json()["total"] == 1


def test_upload_and_delete_source_blocked_on_ready(client):
    bid = client.post(f"{P}/batches", json=CREATE_BODY).json()["id"]
    ids = _seed_sources(client, bid)
    _transition(bid, "checking")
    _transition(bid, "ready")
    again = _upload(client, bid, "claim", "再来一份.xlsx", _xlsx_bytes(_RC_HEADER, [_rc_row()]))
    assert again.status_code == 409
    assert "不可替换" in again.json()["detail"]
    deletion = client.delete(f"{P}/batches/{bid}/sources/{ids['writeoff']}")
    assert deletion.status_code == 409
    assert "不可替换" in deletion.json()["detail"]


def test_delete_source_cascade_via_api(client):
    bid = client.post(f"{P}/batches", json=CREATE_BODY).json()["id"]
    ids = _seed_sources(client, bid)
    assert client.get(f"{P}/batches/{bid}").json()["counts"]["records"] == 7
    r = client.delete(f"{P}/batches/{bid}/sources/{ids['arrear']}")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert client.get(f"{P}/batches/{bid}").json()["counts"]["records"] == 5
    # 已删除/不存在/跨批次的源 → 404
    assert client.delete(f"{P}/batches/{bid}/sources/{ids['arrear']}").status_code == 404
    assert client.delete(f"{P}/batches/{bid}/sources/999999").status_code == 404
    other = client.post(f"{P}/batches", json={**CREATE_BODY, "name": "另一批次"}).json()["id"]
    assert client.delete(f"{P}/batches/{other}/sources/{ids['claim']}").status_code == 404


def test_sources_list_endpoint_fields_contracts_and_safety(client):
    """源文件正式列表（Task 5 前端依赖）：id/kind/原文件名/parse_status/row_count/error/sha256，
    绝不返回存储路径；逐源带检测到的合同列表（scope 勾选依据）。"""
    bid = client.post(f"{P}/batches", json=CREATE_BODY).json()["id"]
    _seed_sources(client, bid)
    # 再造一份解析失败源（非法金额文本 → 400，批次保留 failed 源与摘要）
    bad = _upload(client, bid, "writeoff", "坏样本.xlsx", _xlsx_bytes(_WO_HEADER, [_wo_row(ar_fee="12.3元")]))
    assert bad.status_code == 400

    r = client.get(f"{P}/batches/{bid}/sources")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 5
    items = body["items"]
    assert [i["kind"] for i in items] == ["writeoff", "claim", "arrear", "bill", "writeoff"]
    by_key = {(i["kind"], i["original_name"]): i for i in items}
    ok = by_key[("writeoff", "核销明细样本.xlsx")]
    failed = by_key[("writeoff", "坏样本.xlsx")]
    for item in items:
        assert set(item) == {
            "id", "kind", "kind_label", "original_name", "parse_status",
            "row_count", "error", "sha256", "created_at", "contracts",
        }
        assert len(item["sha256"]) == 64 and all(c in "0123456789abcdef" for c in item["sha256"])
        for value in item.values():  # 任何字段都不含存储路径（盘符/反斜杠/绝对路径）
            assert not (isinstance(value, str) and (":\\" in value or value.startswith("/")))
    assert ok["kind_label"] == "核销" and ok["parse_status"] == "parsed"
    assert ok["row_count"] == 2 and ok["error"] == ""
    assert ok["contracts"] == ["HT-SYN-001", "HT-SYN-002"]
    assert by_key[("claim", "认领记录样本.xlsx")]["contracts"] == []  # 认领行无合同号
    assert by_key[("bill", "账单通知单样本.pdf")]["contracts"] == ["HT-SYN-001"]
    assert failed["parse_status"] == "failed"
    assert "12.3元" in failed["error"] and failed["row_count"] == 0 and failed["contracts"] == []
    # 未知批次 → 404
    assert client.get(f"{P}/batches/777777/sources").status_code == 404
    # 无源批次：空列表（total 0 / items []）
    other_bid = client.post(f"{P}/batches", json={**CREATE_BODY, "name": "空批次无源"}).json()["id"]
    empty = client.get(f"{P}/batches/{other_bid}/sources")
    assert empty.status_code == 200 and empty.json() == {"total": 0, "items": []}


def test_records_pagination_filters_and_caps(client):
    bid = client.post(f"{P}/batches", json=CREATE_BODY).json()["id"]
    _seed_sources(client, bid)
    base = f"{P}/batches/{bid}/records"

    page2 = client.get(base, params={"page": 2, "page_size": 3})
    assert page2.status_code == 200
    body = page2.json()
    assert body["total"] == 7 and body["page"] == 2 and body["page_size"] == 3
    assert len(body["items"]) == 3

    claim_page = client.get(base, params={"kind": "claim"})
    assert claim_page.json()["total"] == 2
    assert all(item["kind"] == "claim" for item in claim_page.json()["items"])

    scoped = client.get(base, params={"contract": "HT-SYN-002"})
    assert scoped.json()["total"] == 2  # 核销第 2 行 + 欠费第 2 行；认领行无合同号

    doc_page = client.get(base, params={"doc": "DZ-SYN-0002"})
    assert doc_page.json()["total"] == 1

    combined = client.get(base, params={"kind": "writeoff", "contract": "HT-SYN-002"})
    assert combined.json()["total"] == 1

    assert client.get(base, params={"page_size": 201}).status_code == 422
    assert client.get(base, params={"page": 0}).status_code == 422
    assert client.get(base, params={"kind": "bogus"}).status_code == 422
    assert client.get(f"{P}/batches/777777/records").status_code == 404


# ---------- 只读 summary 工具 ----------


def test_summary_tool_counts_and_latest(client):
    from app.modules.finance_recon import service

    first = client.post(f"{P}/batches", json=CREATE_BODY).json()["id"]
    second = client.post(
        f"{P}/batches", json={**CREATE_BODY, "name": "批次二", "contract_scope": ["HT-SYN-001"]}
    ).json()["id"]
    _transition(second, "checking")

    summary = service.recon_summary_tool()
    assert summary["batches"] == 2
    assert summary["by_status"]["draft"] == 1
    assert summary["by_status"]["checking"] == 1
    latest = summary["latest"]
    assert latest["id"] == second
    assert latest["status"] == "checking"
    assert latest["counts"]["sources"] == 0
    assert latest["pending_issues"] == 0
    assert latest["suggested_matches"] == 0
    # first 批次随后删除后，工具如实反映
    assert client.delete(f"{P}/batches/{first}").status_code == 200
    assert service.recon_summary_tool()["batches"] == 1


# ---------- Task 3：check / terms / matches / issues / summary 端点 ----------


def _checked_batch(client) -> tuple[int, dict]:
    """上传四类合成源 → 执行 check，返回 (batch_id, check 响应体)。"""
    bid = client.post(f"{P}/batches", json=CREATE_BODY).json()["id"]
    _seed_sources(client, bid)
    r = client.post(f"{P}/batches/{bid}/check")
    assert r.status_code == 200, r.text
    return bid, r.json()


def _record_ids(client, bid: int, kind: str) -> list[int]:
    data = client.get(f"{P}/batches/{bid}/records", params={"kind": kind}).json()
    return [item["id"] for item in data["items"]]


def test_check_endpoint_full_flow_ready_with_diffs(client):
    bid, body = _checked_batch(client)
    assert body["status"] == "ready" and body["needs_recheck"] is False
    assert body["batch_id"] == bid
    assert isinstance(body["checks"], list) and body["checks"]
    assert any(c["state"] == "diff" for c in body["checks"])  # 种子数据刻意不对平
    assert body["totals"]["bill_total"] == "1234.56"
    assert body["totals"]["claim_claimed_total"] == "650.00"
    # 未匹配认领进入问题清单（pending），可诊断
    assert body["issue_stats"]["created"] >= 1
    detail = client.get(f"{P}/batches/{bid}").json()
    assert detail["status"] == "ready" and detail["counts"]["issues"] >= 1
    # ready 批次可重跑
    again = client.post(f"{P}/batches/{bid}/check")
    assert again.status_code == 200 and again.json()["status"] == "ready"


def test_check_endpoint_precheck_failures_return_400(client):
    empty_scope = client.post(f"{P}/batches", json={**CREATE_BODY, "contract_scope": []}).json()["id"]
    r1 = client.post(f"{P}/batches/{empty_scope}/check")
    assert r1.status_code == 400 and "合同" in r1.json()["detail"]
    assert client.get(f"{P}/batches/{empty_scope}").json()["status"] == "draft"

    no_sources = client.post(f"{P}/batches", json=CREATE_BODY).json()["id"]
    r2 = client.post(f"{P}/batches/{no_sources}/check")
    assert r2.status_code == 400 and "缺少源文件" in r2.json()["detail"]
    assert client.get(f"{P}/batches/{no_sources}").json()["status"] == "draft"

    assert client.post(f"{P}/batches/999999/check").status_code == 404


def test_check_endpoint_marks_failed_on_engine_error_then_recovers(client, monkeypatch):
    from app.modules.finance_recon import recon_engine

    bid = client.post(f"{P}/batches", json=CREATE_BODY).json()["id"]
    _seed_sources(client, bid)

    def boom(*args, **kwargs):
        raise RuntimeError("合成勾稽爆炸")

    monkeypatch.setattr(recon_engine, "compute_all", boom)
    r = client.post(f"{P}/batches/{bid}/check")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "failed"
    assert "合成勾稽爆炸" in r.json()["error_summary"]
    assert client.get(f"{P}/batches/{bid}").json()["status"] == "failed"
    monkeypatch.undo()
    r2 = client.post(f"{P}/batches/{bid}/check")
    assert r2.status_code == 200 and r2.json()["status"] == "ready"
    assert client.get(f"{P}/batches/{bid}").json()["error_summary"] == ""


def test_check_endpoint_conflict_while_checking(client):
    from app.database import SessionLocal
    from app.modules.finance_recon import service

    bid = client.post(f"{P}/batches", json=CREATE_BODY).json()["id"]
    _seed_sources(client, bid)
    with SessionLocal() as db:
        service.transition_batch(db, bid, "checking")
    r = client.post(f"{P}/batches/{bid}/check")
    assert r.status_code == 409 and "检查中" in r.json()["detail"]


def test_terms_endpoints_crud_validation_and_guards(client):
    bid = client.post(f"{P}/batches", json=CREATE_BODY).json()["id"]
    empty = client.get(f"{P}/batches/{bid}/terms")
    assert empty.status_code == 200
    # P2-4：GET terms 遍历合同范围，未录入合同返回完整性/缺失项
    items = empty.json()["items"]
    assert [i["contract_no"] for i in items] == SCOPE
    assert all(i["terms"] is None and i["complete"] is False for i in items)
    assert all(i["missing"] == ["未录入口径参数"] for i in items)
    assert empty.json()["out_of_scope_items"] == []
    assert client.get(f"{P}/batches/999999/terms").status_code == 404

    payload = {
        "contract_no": "HT-SYN-001",
        "terms": {
            "contract_start": "2026-01-01",
            "contract_end": "2026-03-20",
            "billing_method": "whole_month",
            "reconstruction_cutoff": "contract_end",
            "monthly_fees": [{"fee_type": "固定租金", "monthly_amount": "1000.00"}],
            "due_rule": "expense_month_day",
            "due_day": 20,
            "penalty": {"rate_per_day": "0.001", "start_rule": "due_date", "cap": "500.00"},
        },
    }
    put = client.put(f"{P}/batches/{bid}/terms", json=payload)
    assert put.status_code == 200, put.text
    item = put.json()
    assert item["contract_no"] == "HT-SYN-001" and item["complete"] is True
    assert item["terms"]["monthly_fees"][0]["monthly_amount"] == "1000.00"  # 金额为字符串
    assert item["terms"]["penalty"]["rate_per_day"] == "0.001"
    got = client.get(f"{P}/batches/{bid}/terms").json()
    assert [i["contract_no"] for i in got["items"]] == SCOPE  # scope 顺序：001 在前
    saved = next(i for i in got["items"] if i["contract_no"] == "HT-SYN-001")
    assert saved["complete"] is True and saved["missing"] == []
    unsaved = next(i for i in got["items"] if i["contract_no"] == "HT-SYN-002")
    assert unsaved["terms"] is None and "未录入口径参数" in unsaved["missing"]

    # P1-4：合同起止倒置 → 422（两边都填才校验）
    inverted = {**payload, "terms": {**payload["terms"], "contract_end": "2025-01-01"}}
    assert client.put(f"{P}/batches/{bid}/terms", json=inverted).status_code == 422
    # P2-7：缺必填 reconstruction_cutoff → 422（引擎不擅自决定截止边界）
    no_cutoff = {k: v for k, v in payload["terms"].items() if k != "reconstruction_cutoff"}
    assert client.put(
        f"{P}/batches/{bid}/terms", json={"contract_no": "HT-SYN-001", "terms": no_cutoff}
    ).status_code == 422

    # 非法值一律 422：未知计费方式 / 空 contract_no / due_day 越界 / 多余字段 / 负金额
    bad_billing = {**payload, "terms": {**payload["terms"], "billing_method": "magic"}}
    assert client.put(f"{P}/batches/{bid}/terms", json=bad_billing).status_code == 422
    blank = {**payload, "contract_no": "  "}
    assert client.put(f"{P}/batches/{bid}/terms", json=blank).status_code == 422
    bad_day = {**payload, "terms": {**payload["terms"], "due_day": 32}}
    assert client.put(f"{P}/batches/{bid}/terms", json=bad_day).status_code == 422
    extra = {**payload, "unexpected": 1}
    assert client.put(f"{P}/batches/{bid}/terms", json=extra).status_code == 422
    negative = {**payload, "terms": {**payload["terms"], "monthly_fees": [
        {"fee_type": "固定租金", "monthly_amount": "-1.00"}]}}
    assert client.put(f"{P}/batches/{bid}/terms", json=negative).status_code == 422

    # checking 批次禁改 → 409
    _transition(bid, "checking")
    assert client.put(f"{P}/batches/{bid}/terms", json=payload).status_code == 409
    _transition(bid, "failed")
    put2 = client.put(f"{P}/batches/{bid}/terms", json=payload)
    assert put2.status_code == 200
    assert client.get(f"{P}/batches/{bid}").json()["needs_recheck"] is False  # 非 ready 不置位


def test_terms_put_on_ready_sets_needs_recheck_and_rerun_clears(client):
    bid, _ = _checked_batch(client)
    payload = {
        "contract_no": "HT-SYN-001",
        "terms": {
            "billing_method": "whole_month",
            "reconstruction_cutoff": "contract_end",
            "monthly_fees": [{"fee_type": "固定租金", "monthly_amount": "1000.00"}],
        },
    }
    put = client.put(f"{P}/batches/{bid}/terms", json=payload)
    assert put.status_code == 200, put.text
    detail = client.get(f"{P}/batches/{bid}").json()
    assert detail["needs_recheck"] is True
    # 导出守卫：needs_recheck 未重跑前必须 409
    from app.modules.finance_recon import service

    with pytest.raises(service.ReconConflictError) as exc:
        with __import__("app.database", fromlist=["SessionLocal"]).SessionLocal() as db:
            service.assert_exportable(db, bid)
    assert "重跑" in str(exc.value) or "重新执行" in str(exc.value)
    # 重跑恢复
    rerun = client.post(f"{P}/batches/{bid}/check")
    assert rerun.status_code == 200 and rerun.json()["status"] == "ready"
    assert client.get(f"{P}/batches/{bid}").json()["needs_recheck"] is False
    with __import__("app.database", fromlist=["SessionLocal"]).SessionLocal() as db:
        service.assert_exportable(db, bid)


def test_matches_endpoints_flow_with_over_allocation_409(client):
    bid = client.post(f"{P}/batches", json=CREATE_BODY).json()["id"]
    _seed_sources(client, bid)
    claims = _record_ids(client, bid, "claim")
    writeoffs = _record_ids(client, bid, "writeoff")
    assert len(claims) == 2 and len(writeoffs) == 2
    assert client.get(f"{P}/batches/{bid}/matches").json()["total"] == 0

    r1 = client.post(f"{P}/batches/{bid}/matches", json={
        "claim_record_id": claims[0], "writeoff_record_id": writeoffs[0],
        "allocated_amount": "100.00", "note": "对应第一期",
    })
    assert r1.status_code == 201, r1.text
    match1 = r1.json()
    assert match1["status"] == "suggested" and match1["origin"] == "manual"
    assert match1["allocated_amount"] == "100.00"
    assert match1["claim"]["doc_no"] and match1["writeoff"]["doc_no"]

    dup = client.post(f"{P}/batches/{bid}/matches", json={
        "claim_record_id": claims[0], "writeoff_record_id": writeoffs[0],
        "allocated_amount": "50.00",
    })
    assert dup.status_code == 409 and "唯一" in dup.json()["detail"]
    assert client.post(f"{P}/batches/{bid}/matches", json={
        "claim_record_id": 999999, "writeoff_record_id": writeoffs[0], "allocated_amount": "1.00",
    }).status_code == 404
    assert client.post(f"{P}/batches/{bid}/matches", json={
        "claim_record_id": writeoffs[0], "writeoff_record_id": claims[0], "allocated_amount": "1.00",
    }).status_code == 400  # kind 颠倒
    assert client.post(f"{P}/batches/{bid}/matches", json={
        "claim_record_id": claims[0], "writeoff_record_id": writeoffs[1], "allocated_amount": "0.00",
    }).status_code == 422

    confirm1 = client.patch(f"{P}/matches/{match1['id']}", json={"status": "confirmed", "note": "已核对"})
    assert confirm1.status_code == 200 and confirm1.json()["status"] == "confirmed"
    # 超额：认领 500 已确认 100，再确认 200 超认领侧 → 409
    r2 = client.post(f"{P}/batches/{bid}/matches", json={
        "claim_record_id": claims[0], "writeoff_record_id": writeoffs[1],
        "allocated_amount": "200.00",
    })
    assert r2.status_code == 201, r2.text
    match2 = r2.json()
    over = client.patch(f"{P}/matches/{match2['id']}", json={"status": "confirmed"})
    assert over.status_code == 409 and "分摊" in over.json()["detail"]
    ok = client.patch(f"{P}/matches/{match2['id']}", json={"allocated_amount": "100.00"})
    assert ok.status_code == 200
    assert client.patch(f"{P}/matches/{match2['id']}", json={"status": "confirmed"}).status_code == 200
    # 冻结：confirmed 行禁止改分摊；rejected 可回 suggested；非法状态 422；空 PATCH 400
    frozen = client.patch(f"{P}/matches/{match2['id']}", json={"allocated_amount": "1.00"})
    assert frozen.status_code == 409 and "冻结" in frozen.json()["detail"]
    back = client.patch(f"{P}/matches/{match2['id']}", json={"status": "suggested"})
    assert back.status_code == 200 and back.json()["status"] == "suggested"
    assert client.patch(f"{P}/matches/{match2['id']}", json={"status": "bogus"}).status_code == 422
    assert client.patch(f"{P}/matches/{match2['id']}", json={}).status_code == 400
    assert client.patch(f"{P}/matches/999999", json={"status": "confirmed"}).status_code == 404

    data = client.get(f"{P}/batches/{bid}/matches").json()
    assert data["total"] == 2
    assert {item["status"] for item in data["items"]} == {"suggested", "confirmed"}


def test_issues_endpoints_flow_note_required_and_filters(client):
    bid = client.post(f"{P}/batches", json=CREATE_BODY).json()["id"]
    _seed_sources(client, bid)
    assert client.get(f"{P}/batches/{bid}/issues").json()["total"] == 0

    created = client.post(f"{P}/batches/{bid}/issues", json={
        "category": "待补资料", "severity": "info", "note": "",
        "amount_impact": "0.00",
    })
    assert created.status_code == 201, created.text
    issue = created.json()
    assert issue["origin"] == "human" and issue["status"] == "pending" and issue["active"] is True

    # 确认/排除必须带说明（400）；非法状态/严重级别 422；空 PATCH 400
    no_note = client.patch(f"{P}/issues/{issue['id']}", json={"status": "confirmed"})
    assert no_note.status_code == 400 and "说明" in no_note.json()["detail"]
    assert client.patch(f"{P}/issues/{issue['id']}", json={"status": "bogus"}).status_code == 422
    assert client.patch(f"{P}/issues/{issue['id']}", json={"severity": "loud"}).status_code == 422
    assert client.patch(f"{P}/issues/{issue['id']}", json={}).status_code == 400
    assert client.patch(f"{P}/issues/999999", json={"note": "x"}).status_code == 404

    confirmed = client.patch(f"{P}/issues/{issue['id']}", json={
        "status": "confirmed", "note": "已向商户索取，挂在 11 月跟进",
    })
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed" and confirmed.json()["active"] is True
    assert confirmed.json()["note"] == "已向商户索取，挂在 11 月跟进"

    # 机检问题由 check 生成；human 问题重跑不被触碰
    bid2, body = _checked_batch(client)
    human2 = client.post(f"{P}/batches/{bid2}/issues", json={"category": "待补资料", "note": "x"}).json()
    assert body["issue_stats"]["created"] >= 1  # 未匹配认领等机检问题
    client.post(f"{P}/batches/{bid2}/check")
    got = client.get(f"{P}/batches/{bid2}/issues").json()
    h = next(i for i in got["items"] if i["id"] == human2["id"])
    assert h["origin"] == "human" and h["status"] == "pending" and h["note"] == "x"
    m = [i for i in got["items"] if i["origin"] == "machine"]
    assert m and all(i["status"] in ("pending", "resolved") for i in m)

    # 过滤参数
    flt = client.get(f"{P}/batches/{bid2}/issues", params={"origin": "human"}).json()
    assert flt["total"] == 1 and flt["items"][0]["id"] == human2["id"]
    flt2 = client.get(f"{P}/batches/{bid}/issues", params={"status": "confirmed"}).json()
    assert flt2["total"] == 1


def test_summary_endpoint_and_agent_tool_extension(client):
    bid, _ = _checked_batch(client)
    s = client.get(f"{P}/batches/{bid}/summary")
    assert s.status_code == 200, s.text
    body = s.json()
    assert body["batch"]["id"] == bid and body["batch"]["status"] == "ready"
    assert body["totals"]["bill_total"] == "1234.56"
    assert set(body["check_states"]) == {"pass", "diff", "unknown", "info"}
    assert body["issues"]["counts"]["pending"] >= 1
    assert body["matches"]["counts"]["suggested"] >= 0
    assert client.get(f"{P}/batches/999999/summary").status_code == 404

    from app.modules.finance_recon import service

    tool = service.recon_summary_tool()
    latest = tool["latest"]
    assert latest["id"] == bid
    assert latest["pending_issues"] >= 1
    assert latest["totals"]["bill_total"] == "1234.56"
    assert set(latest["check_states"]) == {"pass", "diff", "unknown", "info"}


# ---------- P2-1：ever_ready 审计留痕 API 守卫（曾 ready 批次重跑 failed） ----------


def test_ever_ready_failed_batch_api_guards(client):
    bid = client.post(f"{P}/batches", json=CREATE_BODY).json()["id"]
    ids = _seed_sources(client, bid)
    assert client.post(f"{P}/batches/{bid}/check").json()["status"] == "ready"

    from app.database import SessionLocal
    from app.modules.finance_recon.models import ReconBatch

    with SessionLocal() as db:
        batch = db.get(ReconBatch, bid)
        batch.status = "failed"
        batch.error_summary = "勾稽检查失败：模拟"
        db.commit()

    detail = client.get(f"{P}/batches/{bid}").json()
    assert detail["status"] == "failed" and detail["ever_ready"] is True

    del_r = client.delete(f"{P}/batches/{bid}")
    assert del_r.status_code == 409 and "审计留痕" in del_r.json()["detail"]
    patch_r = client.patch(f"{P}/batches/{bid}", json={"contract_scope": ["HT-OTHER"]})
    assert patch_r.status_code == 409 and "审计留痕" in patch_r.json()["detail"]
    upload_r = _upload(client, bid, "claim", "替换源.xlsx", _xlsx_bytes(_RC_HEADER, [_rc_row()]))
    assert upload_r.status_code == 409 and "不可替换" in upload_r.json()["detail"]
    del_src = client.delete(f"{P}/batches/{bid}/sources/{ids['claim']}")
    assert del_src.status_code == 409 and "不可替换" in del_src.json()["detail"]

    # terms 仍可修改并重跑恢复 ready
    put = client.put(f"{P}/batches/{bid}/terms", json={
        "contract_no": "HT-SYN-001",
        "terms": {"billing_method": "whole_month", "reconstruction_cutoff": "contract_end",
                  "monthly_fees": [{"fee_type": "固定租金", "monthly_amount": "1000.00"}]},
    })
    assert put.status_code == 200, put.text
    assert client.get(f"{P}/batches/{bid}").json()["needs_recheck"] is True
    rerun = client.post(f"{P}/batches/{bid}/check")
    assert rerun.status_code == 200 and rerun.json()["status"] == "ready"
    assert client.get(f"{P}/batches/{bid}").json()["needs_recheck"] is False


# ---------- I2：/check 写入冲突（唯一约束等）映射真实 409，不得 200 failed ----------


def test_check_endpoint_conflict_maps_to_409_with_failed_summary(client, monkeypatch):
    from app.modules.finance_recon import recon_engine, service

    bid = client.post(f"{P}/batches", json=CREATE_BODY).json()["id"]
    _seed_sources(client, bid)

    def boom(db, batch_id, drafts):
        raise service.ReconConflictError("机检问题同步：唯一约束冲突，请检查是否重复提交")

    monkeypatch.setattr(recon_engine, "_sync_issues", boom)
    r = client.post(f"{P}/batches/{bid}/check")
    assert r.status_code == 409, r.text
    assert "唯一约束" in r.json()["detail"]
    detail = client.get(f"{P}/batches/{bid}").json()
    assert detail["status"] == "failed"
    assert "唯一约束" in detail["error_summary"]

    # 冲突回收为 failed 后可重试恢复 ready（无半提交、可继续）
    monkeypatch.undo()
    r2 = client.post(f"{P}/batches/{bid}/check")
    assert r2.status_code == 200 and r2.json()["status"] == "ready"
    assert client.get(f"{P}/batches/{bid}").json()["error_summary"] == ""
