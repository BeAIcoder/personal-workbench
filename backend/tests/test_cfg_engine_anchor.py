"""分项D：四个财务引擎目录推导统一走 utils.find_sibling_project（资产锚点可配置）。

约定：
- 全部结构在 tmp_path 下伪造（锚点目录/兄弟项目/假模块文件），不触碰真实AssetLib；
- 各模块函数内部以模块全局 __file__ 定位自身，测试经 monkeypatch 改 __file__ 指向假结构；
- 锚点留空时兜底「01-财税自动化」，行为与改造前一致；锚点找不到时保持原有错误处理
  （expense 返回 None、cashflow/pos 抛中文 EngineUnavailableError、invoice 退回固定层级路径）。
"""
from pathlib import Path

import pytest

from app.config import settings
from app.modules.finance_cashflow import service as cashflow_svc
from app.modules.finance_expense import service as expense_svc
from app.modules.finance_invoice import engine as invoice_engine
from app.modules.finance_pos import service as pos_svc

CUSTOM_ANCHOR = "自定义资产锚点"


def _fake_module_file(tmp_path: Path, anchor: str, module_dir: str, filename: str = "service.py") -> Path:
    """在 tmp_path/<anchor>/personal-workbench/backend/app/modules/<module_dir>/ 下造假模块文件。"""
    fake = tmp_path / anchor / "personal-workbench" / "backend" / "app" / "modules" / module_dir
    fake.mkdir(parents=True, exist_ok=True)
    f = fake / filename
    f.write_text("", encoding="utf-8")
    return f


def _make_expense_engine(root: Path) -> Path:
    """按 _is_engine_dir 口径造齐费用引擎必需文件。"""
    engine = root / "现金流滚动预测" / "费用分类汇总系统-v2.0"
    engine.mkdir(parents=True, exist_ok=True)
    for name in expense_svc._ENGINE_REQUIRED_FILES:
        (engine / name).write_text("", encoding="utf-8")
    return engine


# ---------- finance_expense ----------

def test_expense_engine_dir_follows_custom_anchor(tmp_path, monkeypatch):
    fake_file = _fake_module_file(tmp_path, CUSTOM_ANCHOR, "finance_expense")
    engine = _make_expense_engine(tmp_path / CUSTOM_ANCHOR)
    monkeypatch.setattr(settings, "finance_asset_anchor", CUSTOM_ANCHOR)
    monkeypatch.setattr(settings, "finance_expense_engine_path", "")
    monkeypatch.setattr(expense_svc, "__file__", str(fake_file))

    assert expense_svc.find_engine_dir() == engine


def test_expense_engine_dir_default_anchor_fallback(tmp_path, monkeypatch):
    """锚点配置留空时兜底「01-财税自动化」，默认行为与改造前一致。"""
    fake_file = _fake_module_file(tmp_path, "01-财税自动化", "finance_expense")
    engine = _make_expense_engine(tmp_path / "01-财税自动化")
    monkeypatch.setattr(settings, "finance_asset_anchor", "")
    monkeypatch.setattr(settings, "finance_expense_engine_path", "")
    monkeypatch.setattr(expense_svc, "__file__", str(fake_file))

    assert expense_svc.find_engine_dir() == engine


def test_expense_engine_dir_anchor_missing_returns_none(tmp_path, monkeypatch):
    fake_file = _fake_module_file(tmp_path, "无关目录", "finance_expense")
    monkeypatch.setattr(settings, "finance_asset_anchor", CUSTOM_ANCHOR)
    monkeypatch.setattr(settings, "finance_expense_engine_path", "")
    monkeypatch.setattr(expense_svc, "__file__", str(fake_file))

    assert expense_svc.find_engine_dir() is None


# ---------- finance_invoice ----------

def test_invoice_default_root_follows_custom_anchor(tmp_path, monkeypatch):
    fake_file = _fake_module_file(tmp_path, CUSTOM_ANCHOR, "finance_invoice", "engine.py")
    monkeypatch.setattr(settings, "finance_asset_anchor", CUSTOM_ANCHOR)
    monkeypatch.setattr(invoice_engine, "__file__", str(fake_file))

    assert invoice_engine.default_engine_root() == (
        tmp_path / CUSTOM_ANCHOR / "发票工具" / "发票批量重命名增加项目"
    )


def test_invoice_default_root_anchor_missing_keeps_fixed_fallback(tmp_path, monkeypatch):
    """锚点找不到时退回原固定层级推导（parents[5]），路径有效性由调用方中文报不可用。"""
    fake_file = _fake_module_file(tmp_path, "无关目录", "finance_invoice", "engine.py")
    monkeypatch.setattr(settings, "finance_asset_anchor", CUSTOM_ANCHOR)
    monkeypatch.setattr(invoice_engine, "__file__", str(fake_file))

    assert invoice_engine.default_engine_root() == (
        fake_file.resolve().parents[5] / "发票工具" / "发票批量重命名增加项目"
    )


# ---------- finance_cashflow ----------

def test_cashflow_engine_dir_follows_custom_anchor(tmp_path, monkeypatch):
    fake_file = _fake_module_file(tmp_path, CUSTOM_ANCHOR, "finance_cashflow")
    monkeypatch.setattr(settings, "finance_asset_anchor", CUSTOM_ANCHOR)
    monkeypatch.setattr(settings, "finance_cashflow_engine_path", "")
    monkeypatch.setattr(cashflow_svc, "__file__", str(fake_file))

    assert cashflow_svc.engine_dir() == (
        tmp_path / CUSTOM_ANCHOR / "现金流滚动预测" / "现金流滚动预测表自动化填报" / "V1"
    )


def test_cashflow_engine_dir_anchor_missing_raises_chinese(tmp_path, monkeypatch):
    fake_file = _fake_module_file(tmp_path, "无关目录", "finance_cashflow")
    monkeypatch.setattr(settings, "finance_asset_anchor", CUSTOM_ANCHOR)
    monkeypatch.setattr(settings, "finance_cashflow_engine_path", "")
    monkeypatch.setattr(cashflow_svc, "__file__", str(fake_file))

    with pytest.raises(cashflow_svc.EngineUnavailableError, match="无法定位现金流引擎目录，请配置 finance_cashflow_engine_path"):
        cashflow_svc.engine_dir()


# ---------- finance_pos ----------

def test_pos_engine_dir_follows_custom_anchor(tmp_path, monkeypatch):
    fake_file = _fake_module_file(tmp_path, CUSTOM_ANCHOR, "finance_pos")
    monkeypatch.setattr(settings, "finance_asset_anchor", CUSTOM_ANCHOR)
    monkeypatch.setattr(settings, "finance_pos_engine_path", "")
    monkeypatch.setattr(pos_svc, "__file__", str(fake_file))

    assert pos_svc.engine_dir() == tmp_path / CUSTOM_ANCHOR / "收款认领POS台账"


def test_pos_engine_dir_anchor_missing_raises_chinese(tmp_path, monkeypatch):
    fake_file = _fake_module_file(tmp_path, "无关目录", "finance_pos")
    monkeypatch.setattr(settings, "finance_asset_anchor", CUSTOM_ANCHOR)
    monkeypatch.setattr(settings, "finance_pos_engine_path", "")
    monkeypatch.setattr(pos_svc, "__file__", str(fake_file))

    with pytest.raises(pos_svc.EngineUnavailableError, match="无法定位 POS 引擎目录，请配置 finance_pos_engine_path"):
        pos_svc.engine_dir()


# ---------- 配置优先逻辑不被动摇 ----------

def test_configured_engine_path_still_takes_precedence(tmp_path, monkeypatch):
    """settings.finance_*_engine_path 非空时仍只用配置值，锚点推导不参与。"""
    configured = tmp_path / "指定引擎"
    configured.mkdir()
    monkeypatch.setattr(settings, "finance_asset_anchor", CUSTOM_ANCHOR)

    monkeypatch.setattr(settings, "finance_cashflow_engine_path", str(configured))
    assert cashflow_svc.engine_dir() == configured

    monkeypatch.setattr(settings, "finance_pos_engine_path", str(configured))
    assert pos_svc.engine_dir() == configured

    monkeypatch.setattr(settings, "finance_invoice_engine_path", str(configured))
    assert invoice_engine.engine_root() == configured

    # expense 的配置值须通过 _is_engine_dir 校验
    engine = _make_expense_engine(tmp_path)
    monkeypatch.setattr(settings, "finance_expense_engine_path", str(engine))
    assert expense_svc.find_engine_dir() == engine
