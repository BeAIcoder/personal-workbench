"""finance_bankflow 路由：流水解析预览 + 模板生成下载。"""

from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from ...upload import UploadTooLargeError, read_upload_limited
from .service import (
    build_template,
    export_filename,
    load_account_map,
    mask_account,
    parse_flow,
    resolve_account,
    resolve_account_token,
    summarize_flow,
)

router = APIRouter()

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_MAX_UPLOAD = 20 * 1024 * 1024  # 20MB 上限
_CONFIG_HINT = "（详见模块内 config.local.example.yaml，复制为 config.local.yaml 后填写 bank_accounts）"


def _bad(msg: str) -> HTTPException:
    return HTTPException(status_code=400, detail=msg)


async def _read_upload(file: UploadFile) -> bytes:
    """经共享 read_upload_limited 分块限额读取（超限在读取期中断，不再是先读后限）。"""
    return await read_upload_limited(file, _MAX_UPLOAD)


def _validate_date(s: str | None) -> str | None:
    """YYYY-MM-DD 校验并转 YYYYMMDD；空返回 None。"""
    if not s:
        return None
    text = str(s).strip().replace("/", "-")
    try:
        return datetime.strptime(text, "%Y-%m-%d").strftime("%Y%m%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM-DD")


def _resolve_request_account(rows: list[dict], account: str, account_token: str) -> str | None:
    """导出请求的账号解析：令牌优先（服务端反查上传流水），仅兼容预览掩码入参。

    - account_token：只在本次上传流水中出现的账号里匹配，令牌与文件不匹配一律拒绝；
    - 掩码账号（含 ``*``）：按掩码形式在流水中唯一匹配，歧义（同尾号多账户）要求改用令牌；
    - 完整账号不再接受（应由预览返回的令牌替代，避免客户端重新引入 PII 通道）。
    """
    unique = list(dict.fromkeys(r["account"] for r in rows))
    token = (account_token or "").strip()
    if token:
        return resolve_account_token(token, unique)
    legacy = (account or "").strip()
    if legacy and "*" in legacy:
        matches = [a for a in unique if mask_account(a) == legacy]
        return matches[0] if len(matches) == 1 else None
    return None


def _attachment(name: str) -> dict[str, str]:
    """下载响应头：ASCII 兜底 + filename*（照发票模块口径，防中文名丢失）。"""
    return {
        "Content-Disposition": (
            f'attachment; filename="bankflow_template.xlsx"; '
            f"filename*=utf-8''{quote(name)}"
        )
    }


@router.post("/preview", summary="解析流水文件并按账号汇总预览（异常行计数 + 中文提示）")
async def preview(file: UploadFile = File(...)):
    try:
        data = await _read_upload(file)
    except UploadTooLargeError as exc:
        raise _bad(f"流水文件超过 {_MAX_UPLOAD // 1024 // 1024}MB 上限") from exc
    try:
        return summarize_flow(data)
    except ValueError as exc:
        raise _bad(str(exc)) from exc


@router.post("/template", summary="按账号令牌与日期区间生成 BIP 银行流水导入模板（直接下载 xlsx）")
async def template_endpoint(
    file: UploadFile = File(...),
    account: str = Form(default=""),
    account_token: str = Form(default=""),
    date_start: str = Form(default=""),
    date_end: str = Form(default=""),
):
    try:
        data = await _read_upload(file)
    except UploadTooLargeError as exc:
        raise _bad(f"流水文件超过 {_MAX_UPLOAD // 1024 // 1024}MB 上限") from exc
    start = _validate_date(date_start)
    end = _validate_date(date_end)
    token = (account_token or "").strip()
    legacy = (account or "").strip()
    if bool(token) == bool(legacy):
        raise _bad("account_token 与 account 必须二选一：请使用预览返回的账号令牌")
    if legacy and "*" not in legacy:
        raise _bad("不再接受完整银行账号，请使用预览返回的账号令牌导出")
    acc_map = load_account_map()
    if not acc_map:
        raise _bad("未配置银行账号 → H 开头账套编码映射（config.local.yaml bank_accounts）" + _CONFIG_HINT)
    try:
        rows = parse_flow(data)
    except ValueError as exc:
        raise _bad(str(exc)) from exc
    resolved = _resolve_request_account(rows, legacy, token)
    if resolved is None:
        raise _bad("账号令牌无效或与上传流水不匹配，请在预览页重新选择账号后再导出")
    meta = resolve_account(acc_map, resolved)
    if meta is None:
        raise _bad(f"账号 {mask_account(resolved)} 未在映射配置中登记，无法生成模板" + _CONFIG_HINT)
    ent = meta["ent"]
    try:
        content, exported = build_template(rows, resolved, ent, start, end)
    except ValueError as exc:
        raise _bad(str(exc)) from exc
    if exported == 0:
        raise _bad("所选日期区间内无该账号的流水记录，未生成模板")
    bank = next((r.get("bank") or "" for r in rows if r["account"] == resolved), "")
    name = export_filename(resolved, date_start, date_end, bank)
    return Response(
        content=content,
        media_type=_XLSX_MIME,
        headers=_attachment(name),
    )


@router.get("/accounts", summary="已登记的账号映射（账号仅展示尾 4 位，防止完整卡号外泄）")
def accounts():
    acc_map = load_account_map()
    items = [
        {
            # 前端只需 tail/ent/name 展示；完整账号仅在预览/生成请求内使用
            "account": f"{'*' * max(0, len(acc) - 4)}{acc[-4:]}",
            "tail": acc[-4:],
            "ent": info.get("ent") or "",
            "name": info.get("name") or "",
        }
        for acc, info in acc_map.items()
    ]
    return {"items": items}