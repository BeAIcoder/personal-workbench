"""finance_recon 模块接口：/api/finance/recon（前缀由注册器按 manifest.prefix 挂载）。

Task 1 批次五类端点；Task 2 源文件上传/删除与事实记录分页查询；
Task 3：check / summary / terms / matches / issues（导出由 Task 4 追加，
不提供 501 或假实现占位）。
"""
import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ...database import get_db
from ...upload import UploadTooLargeError, read_upload_limited
from ...utils import repair_upload_mojibake
from . import exporters, importer, matching, reporting, recon_engine, service
from .schemas import (
    IssueCreate,
    IssuePatch,
    IssueSeverity,
    IssueStatus,
    MatchCreate,
    MatchPatch,
    ReconBatchCreate,
    ReconBatchPatch,
    SourceKind,
    TermsUpsert,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["财务模块·商户核销对账"])


def _not_found(exc: service.ReconNotFoundError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))


def _bad_request(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


def _conflict(exc: service.ReconConflictError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


@router.post("/batches", summary="新建商户核销对账批次（draft）", status_code=201)
def create_batch(payload: ReconBatchCreate, db: Session = Depends(get_db)):
    try:
        return service.create_batch(db, payload)
    except service.ReconNotFoundError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise _bad_request(exc) from exc
    except service.ReconConflictError as exc:
        raise _conflict(exc) from exc


@router.get("/batches", summary="批次列表（最新在前，精简视图）")
def list_batches(limit: int = Query(default=50, ge=1, le=200), db: Session = Depends(get_db)):
    return service.list_batches(db, limit)


@router.get("/batches/{batch_id}", summary="批次详情（含合同范围与子表计数）")
def get_batch(batch_id: int, db: Session = Depends(get_db)):
    try:
        return service.get_batch(db, batch_id)
    except service.ReconNotFoundError as exc:
        raise _not_found(exc) from exc


@router.patch("/batches/{batch_id}", summary="维护批次（仅 draft/failed：名称、对账日、容差、合同范围）")
def patch_batch(batch_id: int, payload: ReconBatchPatch, db: Session = Depends(get_db)):
    try:
        return service.update_batch(db, batch_id, payload)
    except service.ReconNotFoundError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise _bad_request(exc) from exc
    except service.ReconConflictError as exc:
        raise _conflict(exc) from exc


@router.delete("/batches/{batch_id}", summary="删除批次（仅 draft/failed，级联清理五张子表；ready 为审计留痕）")
def delete_batch(batch_id: int, db: Session = Depends(get_db)):
    try:
        return service.delete_batch(db, batch_id)
    except service.ReconNotFoundError as exc:
        raise _not_found(exc) from exc
    except service.ReconConflictError as exc:
        raise _conflict(exc) from exc


# ---------- Task 2：源文件上传 / 删除 / 事实记录查询 ----------


@router.post(
    "/batches/{batch_id}/sources/{kind}",
    status_code=201,
    summary="上传四类源文件之一（仅 draft/failed；重复内容返回 skipped_duplicates 不重复导入）",
)
async def upload_source(
    batch_id: int,
    kind: SourceKind,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    filename = (file.filename or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    filename = repair_upload_mojibake(filename)
    try:
        # P2-1：曾就绪批次的源文件不可替换（router 层守卫，importer 未在本轮允许清单内）
        batch = service._get_batch(db, batch_id)
        service.assert_sources_replacable(batch)
    except service.ReconNotFoundError as exc:
        raise _not_found(exc) from exc
    except service.ReconConflictError as exc:
        raise _conflict(exc) from exc
    try:
        content = await read_upload_limited(file, importer.MAX_UPLOAD_BYTES)
    except UploadTooLargeError:
        raise  # main.py 全局映射 400（保留精确的上限提示）
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("recon 上传读取失败（上限 %s 字节）：%s", importer.MAX_UPLOAD_BYTES, exc)
        raise HTTPException(
            status_code=400,
            detail=f"文件读取失败或超过 {importer.MAX_UPLOAD_BYTES // 1024 // 1024}MB 上限",
        ) from exc
    try:
        result = importer.import_source(db, batch_id, kind, (filename, content))
    except service.ReconNotFoundError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:  # 含 ParseSourceError（文件/工作表/行/单元格定位）
        raise _bad_request(exc) from exc
    except service.ReconConflictError as exc:
        raise _conflict(exc) from exc
    payload = result.as_dict()
    if result.skipped_duplicates:
        # 重复内容未新建源记录：返回 200（非创建语义），携带 skipped_duplicates
        return JSONResponse(status_code=200, content=payload)
    return payload  # 新建源记录：201


@router.delete(
    "/batches/{batch_id}/sources/{source_id}",
    summary="删除源文件（仅 draft/failed；级联 records+suggested matches；人工引用 409）",
)
def delete_source(batch_id: int, source_id: int, db: Session = Depends(get_db)):
    try:
        return service.delete_source(db, batch_id, source_id)
    except service.ReconNotFoundError as exc:
        raise _not_found(exc) from exc
    except service.ReconConflictError as exc:
        raise _conflict(exc) from exc


@router.get(
    "/batches/{batch_id}/sources",
    summary="源文件列表（含解析状态/失败原因/检测到的合同；绝不返回存储路径）",
)
def list_sources(batch_id: int, db: Session = Depends(get_db)):
    try:
        return service.list_sources(db, batch_id)
    except service.ReconNotFoundError as exc:
        raise _not_found(exc) from exc


@router.get(
    "/batches/{batch_id}/records",
    summary="事实记录分页查询（kind/合同/单据过滤；page_size≤200）",
)
def list_records(
    batch_id: int,
    kind: SourceKind | None = Query(default=None),
    contract: str | None = Query(default=None, max_length=80),
    doc: str | None = Query(default=None, max_length=80),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    try:
        return service.list_records(
            db, batch_id, kind=kind, contract=contract, doc=doc, page=page, page_size=page_size
        )
    except service.ReconNotFoundError as exc:
        raise _not_found(exc) from exc


# ---------- Task 3：check / summary / terms / matches / issues ----------


@router.post("/batches/{batch_id}/check", summary="执行勾稽检查（precheck→checking→ready|failed）")
def run_check(batch_id: int, db: Session = Depends(get_db)):
    try:
        return recon_engine.run_checks(db, batch_id)
    except service.ReconNotFoundError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:  # precheck：范围/来源/解析失败
        raise _bad_request(exc) from exc
    except service.ReconConflictError as exc:  # 非法状态迁移
        raise _conflict(exc) from exc


@router.get("/batches/{batch_id}/summary", summary="批次勾稽摘要（统一报告视图，含合计/检查状态/待确认数）")
def batch_summary(batch_id: int, db: Session = Depends(get_db)):
    try:
        return reporting.build_report(db, batch_id).as_dict()
    except service.ReconNotFoundError as exc:
        raise _not_found(exc) from exc


@router.get("/batches/{batch_id}/terms", summary="口径参数清单（逐合同完整性与缺失项）")
def get_terms(batch_id: int, db: Session = Depends(get_db)):
    try:
        return service.get_terms(db, batch_id)
    except service.ReconNotFoundError as exc:
        raise _not_found(exc) from exc


@router.put("/batches/{batch_id}/terms", summary="保存单合同口径参数（ready 批次修改置 needs_recheck）")
def put_terms(batch_id: int, payload: TermsUpsert, db: Session = Depends(get_db)):
    try:
        return service.put_terms(db, batch_id, payload.contract_no, payload.terms)
    except service.ReconNotFoundError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise _bad_request(exc) from exc
    except service.ReconConflictError as exc:
        raise _conflict(exc) from exc


@router.get("/batches/{batch_id}/matches", summary="匹配清单（机检建议 + 人工裁定）")
def list_matches(batch_id: int, db: Session = Depends(get_db)):
    try:
        return matching.list_matches(db, batch_id)
    except service.ReconNotFoundError as exc:
        raise _not_found(exc) from exc


@router.post(
    "/batches/{batch_id}/matches",
    status_code=201,
    summary="人工新增认领↔核销匹配建议（唯一三元组；确认时才校验分摊超额）",
)
def create_match(batch_id: int, payload: MatchCreate, db: Session = Depends(get_db)):
    try:
        return matching.create_manual_match(db, batch_id, payload)
    except service.ReconNotFoundError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise _bad_request(exc) from exc
    except service.ReconConflictError as exc:
        raise _conflict(exc) from exc


@router.patch("/matches/{match_id}", summary="人工裁定匹配：状态/分摊/说明（confirmed/rejected 分摊冻结）")
def patch_match(match_id: int, payload: MatchPatch, db: Session = Depends(get_db)):
    try:
        return matching.patch_match(db, match_id, payload)
    except service.ReconNotFoundError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise _bad_request(exc) from exc
    except service.ReconConflictError as exc:
        raise _conflict(exc) from exc


@router.get("/batches/{batch_id}/issues", summary="问题清单（机检+人工，origin/status/active 过滤）")
def list_issues(
    batch_id: int,
    origin: str | None = Query(default=None, pattern="^(machine|human)$"),
    status: IssueStatus | None = Query(default=None),
    active: bool | None = Query(default=None),
    db: Session = Depends(get_db),
):
    try:
        return matching.list_issues(db, batch_id, origin=origin, status=status, active=active)
    except service.ReconNotFoundError as exc:
        raise _not_found(exc) from exc


@router.post(
    "/batches/{batch_id}/issues",
    status_code=201,
    summary="人工补记问题/待补资料（origin=human；机检永不触碰）",
)
def create_issue(batch_id: int, payload: IssueCreate, db: Session = Depends(get_db)):
    try:
        return matching.create_issue(db, batch_id, payload)
    except service.ReconNotFoundError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise _bad_request(exc) from exc
    except service.ReconConflictError as exc:
        raise _conflict(exc) from exc


@router.patch("/issues/{issue_id}", summary="人工裁定问题：状态/级别/说明（确认或排除必须给说明）")
def patch_issue(issue_id: int, payload: IssuePatch, db: Session = Depends(get_db)):
    try:
        return matching.patch_issue(db, issue_id, payload)
    except service.ReconNotFoundError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise _bad_request(exc) from exc
    except service.ReconConflictError as exc:
        raise _conflict(exc) from exc


# ---------- Task 4：三格式导出（xlsx / report.md / report.docx） ----------

# 下载媒体类型（Content-Disposition 双头统一在 _export_response 拼装）
_EXPORT_MEDIA_TYPES: dict[str, str] = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "md": "text/markdown; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _export_response(view, suffix: str) -> Response:
    """统一下载响应：ASCII filename + filename* UTF-8 中文名（与全站导出惯例一致）。"""
    cn_name, ascii_name = exporters.download_names(view, suffix)
    content: bytes | str
    if suffix == "xlsx":
        content = exporters.export_xlsx(view).getvalue()
    elif suffix == "md":
        content = exporters.export_markdown(view)
    elif suffix == "docx":
        content = exporters.export_docx(view).getvalue()
    else:  # 防御：suffix 只来自下方的固定调用点
        raise ValueError(f"不支持的导出格式 {suffix!r}")
    return Response(
        content=content,
        media_type=_EXPORT_MEDIA_TYPES[suffix],
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(cn_name)}'
            )
        },
    )


def _export(batch_id: int, db: Session, suffix: str) -> Response:
    """导出统一入口：先 assert_exportable（needs_recheck → 409），再由唯一
    ReportViewModel 生成三种格式之一——三个端点不各自重算合计。"""
    try:
        service.assert_exportable(db, batch_id)
        view = reporting.build_report(db, batch_id)
    except service.ReconNotFoundError as exc:
        raise _not_found(exc) from exc
    except service.ReconConflictError as exc:
        raise _conflict(exc) from exc
    return _export_response(view, suffix)


@router.get(
    "/batches/{batch_id}/export/xlsx",
    summary="导出对账工作底稿 xlsx（五类工作表；口径参数改动未重跑时 409）",
)
def export_batch_xlsx(batch_id: int, db: Session = Depends(get_db)):
    return _export(batch_id, db, "xlsx")


@router.get(
    "/batches/{batch_id}/export/report.md",
    summary="导出对账报告 Markdown（与 xlsx/docx 同源；未决事项显著标注草稿）",
)
def export_batch_report_md(batch_id: int, db: Session = Depends(get_db)):
    return _export(batch_id, db, "md")


@router.get(
    "/batches/{batch_id}/export/report.docx",
    summary="导出对账报告 Word（通用排版；未决事项显著标注草稿；needs_recheck 409）",
)
def export_batch_report_docx(batch_id: int, db: Session = Depends(get_db)):
    return _export(batch_id, db, "docx")
