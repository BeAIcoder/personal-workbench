# -*- coding: utf-8 -*-
"""四个税种申报路由的共用逻辑：名称归一、同期间防重、状态守卫与中文文案骨架。

增值税 / 个税 / 印花税 / 残保金四个模块各有一套 ORM 与计税服务，但「同纳税人+同期间
只允许一条申报」「已提交/已导出不可静默改删」的判断与中文提示完全同构。这里只收敛
这部分（复审 M-2），计算、字段、导出仍留在各模块，避免四份拷贝漂移。

两层收敛，边界清晰：

- 模块级函数（``normalize_taxpayer_name`` / ``find_duplicate`` / ``ensure_*``）：
  单个判断的纯函数，供任何调用方复用；
- ``FilingRouter``：把申报记录的七步生命周期（取记录 → 防重 → 落库 → 受控更正 →
  删除+产物清理 → 导出 → 下载）里真正同构的骨架收敛一处。**它不生成路由**：
  路径、摘要、payload、计算、序列化、底稿格式仍由各模块自声明，避免 OpenAPI
  与文案随抽象漂移（架构复盘 5.3：易过度抽象，确认第 5 个税种前不做 factory）。

约定：
- 税种差异通过 ``tax_label``（增值税/个税扣缴/印花税/残保金）、``subject``
  （纳税人/扣缴义务人/用人单位）与 ``period_noun``（期间/年度）描述，
  期间展示由 ``period_noun`` 派生（YYYY-MM 原样，YYYY 加「年度」）；
- 状态判断只看 ``status`` 与 ``export_path``，不感知各模块的自有字段。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from fastapi import HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .utils_filing import unlink_export_artifact

SUBMITTED = "submitted"
EXPORTED = "exported"

_CORRECTION_HINT = "如需变更请联系主管税务机关办理更正申报"


def normalize_taxpayer_name(name: str | None) -> str:
    """纳税人/扣缴义务人/用人单位名归一：空白视为「未命名」，避免空格绕过防重。"""
    return str(name or "").strip()


def find_duplicate(db: Session, model, name_column, period_column,
                   taxpayer_name: str, period: str):
    """同纳税人+同期间的既有记录（model/列由调用方从 ORM 元数据传入）。"""
    return (db.query(model)
            .filter(name_column == taxpayer_name)
            .filter(period_column == period).first())


def submitted_detail(*, tax_label: str, period_display: str, filing_id: int,
                     action: str) -> str:
    """已提交记录的统一 409 文案；action 为「删除」「修改或删除」「更正或删除」。"""
    return (f"该{tax_label}申报（{period_display}，id={filing_id}）已提交，"
            f"已提交记录不可{action}；{_CORRECTION_HINT}")


def duplicate_conflict_detail(*, subject: str, tax_label: str, period_display: str,
                              filing_id: int, status: str, revise_path: str,
                              has_export: bool) -> str:
    """重复 POST 的 409 文案：指向既有记录，并给出更正/删除两条受控路径。"""
    if status == SUBMITTED:
        return submitted_detail(tax_label=tax_label, period_display=period_display,
                                filing_id=filing_id, action="修改或删除")
    if has_export:
        # 已导出记录不能走 PUT 更正；删除会连带清理底稿（既有行为），必须说清
        return (f"该{subject} {period_display}的{tax_label}申报已存在（id={filing_id}）"
                "且已导出底稿：该记录不能直接覆盖更正，请在申报记录列表删除该记录"
                "（会一并删除底稿）后重新填报")
    return (f"该{subject} {period_display}的{tax_label}申报已存在（id={filing_id}）："
            "请在申报记录列表删除该记录后重新填报，"
            f"或调用更正接口 PUT {revise_path} 修改未导出记录")


def ensure_deletable(filing, *, tax_label: str, period_display: str) -> None:
    """已提交记录禁止删除（复审 I-3）：在任何 db.delete/产物清理之前调用。"""
    if filing.status == SUBMITTED:
        raise HTTPException(
            status_code=409,
            detail=submitted_detail(tax_label=tax_label, period_display=period_display,
                                    filing_id=filing.id, action="删除"),
        )


def ensure_revisable(filing, *, tax_label: str, period_display: str) -> None:
    """受控更正的状态守卫：已提交、已导出（含遗留 export_path）都不得静默覆盖。"""
    if filing.status == SUBMITTED:
        raise HTTPException(
            status_code=409,
            detail=submitted_detail(tax_label=tax_label, period_display=period_display,
                                    filing_id=filing.id, action="更正或删除"),
        )
    if filing.status == EXPORTED or filing.export_path:
        raise HTTPException(
            status_code=409,
            detail=("该申报已导出底稿，不能直接覆盖更正；请先删除该记录后重新填报，"
                    f"{_CORRECTION_HINT}"),
        )


def artifact_name(export_path: str | None) -> str:
    """导出产物文件名：只取最后一段，绝对路径/反斜杠/被污染路径都落到导出目录内。"""
    return str(export_path or "").replace("\\", "/").rsplit("/", 1)[-1].strip()


@dataclass(frozen=True)
class FilingSpec:
    """一个税种在「申报记录生命周期」上的差异项（其余行为必须一致）。"""

    model: type
    tax_label: str          # 已提交/已导出文案里的税种名：增值税/个税扣缴/印花税/残保金
    subject: str            # 申报主体：纳税人/扣缴义务人/用人单位
    prefix: str             # 模块路由前缀：/api/finance/vat
    period_column: str      # 所属期间列名：period（YYYY-MM）/ year（YYYY）
    period_noun: str        # 期间/年度（用于文案与期间展示）
    exports_dir: Callable[[], Path | str]

    def period_display(self, period: str) -> str:
        """409 文案里的期间展示：月度期间原样，年度加「年度」后缀。"""
        return period if self.period_noun == "期间" else f"{period} {self.period_noun}"

    def period_of(self, filing) -> str:
        return getattr(filing, self.period_column)


class FilingRouter:
    """四税种申报路由的同构骨架（不生成路由，只收敛一次修四处的判断）。

    收敛的是「七个端点的公共流程 + 守卫顺序」：预检防重 → 唯一索引兜底 → 受控更正
    → 守卫先于删除与产物清理 → 先落库再清底稿 → 导出/下载的文件解析与 410。
    计算、模型字段、序列化、底稿内容由各模块通过参数/回调注入。
    """

    def __init__(self, spec: FilingSpec):
        self.spec = spec

    # ---- 查询与防重 ----

    def duplicate(self, db: Session, taxpayer_name: str, period: str):
        return find_duplicate(db, self.spec.model,
                              getattr(self.spec.model, "taxpayer_name"),
                              getattr(self.spec.model, self.spec.period_column),
                              taxpayer_name, period)

    def conflict_detail(self, dup) -> str:
        return duplicate_conflict_detail(
            subject=self.spec.subject, tax_label=self.spec.tax_label,
            period_display=self.spec.period_display(self.spec.period_of(dup)),
            filing_id=dup.id, status=dup.status,
            revise_path=f"{self.spec.prefix}/filings/{dup.id}",
            has_export=bool(dup.export_path))

    def load(self, db: Session, filing_id: int):
        filing = db.get(self.spec.model, filing_id)
        if filing is None:
            raise HTTPException(status_code=404, detail="填报记录不存在")
        return filing

    def page(self, db: Session, page: int, page_size: int, serialize) -> dict:
        q = db.query(self.spec.model).order_by(self.spec.model.id.desc())
        total = q.count()
        rows = q.offset((page - 1) * page_size).limit(page_size).all()
        return {"total": total, "items": [serialize(f) for f in rows],
                "page": page, "page_size": page_size}

    # ---- 生命周期 ----

    def create(self, db: Session, *, period: str, taxpayer_name: str,
               fields: dict, calculate: Callable[[str], dict]):
        """预检防重 → 计算（ValueError → 400）→ 落库（唯一索引冲突 → 409）。"""
        dup = self.duplicate(db, taxpayer_name, period)
        if dup is not None:
            raise HTTPException(status_code=409, detail=self.conflict_detail(dup))
        calc = self._calculate(calculate, period)
        values = {self.spec.period_column: period, "taxpayer_name": taxpayer_name,
                  "calc_result": calc, "status": "calculated"}
        values.update(fields)
        filing = self.spec.model(**values)
        db.add(filing)
        try:
            db.commit()
        except IntegrityError as exc:  # 并发下预检可能同时通过，唯一索引兜底
            db.rollback()
            dup = self.duplicate(db, taxpayer_name, period)
            raise HTTPException(
                status_code=409,
                detail=self.conflict_detail(dup) if dup is not None
                else (f"该{self.spec.subject}同{self.spec.period_noun}"
                      f"{self.spec.tax_label}申报已存在，请刷新后重试"),
            ) from exc
        db.refresh(filing)
        return filing

    def revise(self, db: Session, filing, *, period_of: Callable[[], str],
               taxpayer_name: str, fields: dict, calculate: Callable[[str], dict]):
        """受控更正：守卫 → 期间/主体不变 → 重算 → 覆盖输入与结果。

        ``period_of`` 必须在「已提交/已导出」守卫之后求值：印花税按季校验（400）
        不得抢在状态守卫（409）之前改变用户看到的失败原因。
        """
        self._ensure_revisable(filing)
        period = period_of()
        if period != self.spec.period_of(filing) or taxpayer_name != filing.taxpayer_name:
            raise HTTPException(
                status_code=400,
                detail=(f"更正不允许更换{self.spec.subject}或所属{self.spec.period_noun}："
                        "请删除该记录后重新填报"),
            )
        calc = self._calculate(calculate, period)
        for key, value in fields.items():
            setattr(filing, key, value)
        filing.calc_result = calc
        filing.status = "calculated"
        filing.error = ""
        db.commit()
        db.refresh(filing)
        return filing

    def delete(self, db: Session, filing_id: int) -> dict:
        filing = self.load(db, filing_id)
        # 已提交留痕不可删除：守卫先于 db.delete/产物清理，底稿原样保留（复审 I-3）
        ensure_deletable(filing, tax_label=self.spec.tax_label,
                         period_display=self.spec.period_display(self.spec.period_of(filing)))
        # 先落库删除、再清产物：commit 失败时不得先丢掉底稿（复核 M-5）
        export_path = filing.export_path
        db.delete(filing)
        db.commit()
        removed = unlink_export_artifact(export_path, self.spec.exports_dir())
        return {"ok": True, "export_removed": removed}

    def export(self, db: Session, filing_id: int, *, exporter,
               not_ready_detail: str, also_not_ready: Callable[[object], bool] | None = None):
        filing = self.load(db, filing_id)
        if not filing.calc_result or (also_not_ready is not None and also_not_ready(filing)):
            raise HTTPException(status_code=409, detail=not_ready_detail)
        path = exporter(filing)
        filing.export_path = str(path)
        filing.status = EXPORTED
        db.commit()
        return {"ok": True, "file": path.name}

    def download(self, db: Session, filing_id: int) -> FileResponse:
        filing = self.load(db, filing_id)
        if not filing.export_path:
            raise HTTPException(status_code=409, detail="尚未导出，请先点「生成底稿」")
        path = Path(self.spec.exports_dir()) / artifact_name(filing.export_path)
        if not path.is_file():
            raise HTTPException(status_code=410, detail="导出文件已不存在，请重新导出")
        return FileResponse(str(path), filename=path.name)

    # ---- 守卫 ----

    def _ensure_revisable(self, filing) -> None:
        ensure_revisable(filing, tax_label=self.spec.tax_label,
                         period_display=self.spec.period_display(self.spec.period_of(filing)))

    @staticmethod
    def _calculate(calculate: Callable[[str], dict], period: str) -> dict:
        try:
            return calculate(period)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
