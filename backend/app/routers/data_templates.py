"""数据模板下载：把仓库 数据模板/ 下的导入模板清单化，供各上传页「下载模板」。

安全口径：文件名走固定白名单（本文件常量），不存在任何用户可控路径拼接；
未命中白名单一律 404，不做路径归一/扫描。
"""
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(tags=["数据模板"])

# 仓库根目录：backend/app/routers/data_templates.py → parents[3]
TEMPLATE_DIR = Path(__file__).resolve().parents[3] / "数据模板"

# 固定清单（文件名, 所属模块）；新增模板先放 数据模板/ 再加一行
TEMPLATES = (
    ("数据仓-入仓模板.xlsx", "数据仓"),
    ("数据仓-入仓模板.csv", "数据仓"),
    ("个人记账-导入模板.csv", "个人记账"),
    ("费用分类-银行存款辅助明细账-模板.xlsx", "费用分类 / 现金流"),
    ("POS对账-认领记录-模板.xlsx", "POS 对账"),
    ("POS对账-辅助明细账-模板.xlsx", "POS 对账"),
    ("POS对账-凭证列表-模板.xlsx", "POS 对账"),
    ("BIP账簿-凭证列表-模板.xlsx", "企业记账 BIP"),
    ("BIP账簿-辅助明细账-模板.xlsx", "企业记账 BIP"),
    ("BIP账簿-辅助余额表-模板.xlsx", "企业记账 BIP"),
)
_BY_NAME = {name: module for name, module in TEMPLATES}


@router.get("/templates", summary="数据模板清单（名称/所属模块/大小/是否就位）")
def list_templates():
    items = []
    for name, module in TEMPLATES:
        path = TEMPLATE_DIR / name
        exists = path.is_file()
        items.append({
            "name": name,
            "module": module,
            "size": path.stat().st_size if exists else 0,
            "available": exists,
        })
    return {"total": len(items), "items": items}


@router.get("/templates/{name}", summary="下载数据模板（白名单文件，中文文件名 RFC5987）")
def download_template(name: str):
    if name not in _BY_NAME:
        raise HTTPException(status_code=404, detail="模板不存在")
    path = TEMPLATE_DIR / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="模板文件缺失，请重新部署后再试")
    return FileResponse(path, filename=name)
