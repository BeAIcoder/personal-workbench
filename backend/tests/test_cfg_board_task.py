"""分项C：报表看板计划任务名/管线目录推导的配置化测试。

只验证配置解析链（_task_name / pipeline_dir），不触发真实管线与计划任务，
不写入真实数据目录。每个用例都显式 monkeypatch settings 字段，
与本机/环境变量里的真实配置隔离。
"""
from pathlib import Path

import pytest

from app.modules.finance_board import service as svc


def test_task_name_default_fallback(monkeypatch):
    """配置为空时回落到 BOARD_TASK_NAME 常量（行为与改造前一致）。"""
    monkeypatch.setattr(svc.settings, "finance_board_task_name", "")
    assert svc._task_name() == svc.BOARD_TASK_NAME


def test_task_name_config_override(monkeypatch):
    """配置非空时优先生效。"""
    monkeypatch.setattr(svc.settings, "finance_board_task_name", "自定义报表任务")
    assert svc._task_name() == "自定义报表任务"


def test_scheduled_task_query_uses_configured_name(monkeypatch):
    """schtasks /query 的任务名参数来自 _task_name()。"""
    seen = {}

    class _Proc:
        returncode = 1  # 假装任务未注册

    def _fake_run(cmd, **_kwargs):
        seen["cmd"] = cmd
        return _Proc()

    monkeypatch.setattr(svc.settings, "finance_board_task_name", "自定义报表任务")
    monkeypatch.setattr(svc.subprocess, "run", _fake_run)
    assert svc._scheduled_task_available() is False
    assert seen["cmd"] == ["schtasks", "/query", "/tn", "自定义报表任务"]


def test_pipeline_dir_configured_path_wins(monkeypatch, tmp_path):
    """配置 finance_board_pipeline_path 优先于锚点推导（原有逻辑不变）。"""
    pdir = tmp_path / "微信报表监控"
    pdir.mkdir()
    monkeypatch.setattr(svc.settings, "finance_board_pipeline_path", str(pdir))
    # 锚点故意设为不存在的名字，若仍返回配置目录即证明配置优先
    monkeypatch.setattr(svc.settings, "finance_asset_anchor", "不存在的锚点xyz")
    assert svc.pipeline_dir() == pdir


def test_pipeline_dir_anchor_from_settings(monkeypatch):
    """锚点目录名取 settings.finance_asset_anchor。"""
    monkeypatch.setattr(svc.settings, "finance_board_pipeline_path", "")
    monkeypatch.setattr(svc.settings, "finance_asset_anchor", "personal-workbench")
    d = svc.pipeline_dir()
    assert d.name == "微信报表监控"
    assert d.parent.name == "personal-workbench"


def test_pipeline_dir_anchor_empty_falls_back(monkeypatch):
    """锚点配置为空时兜底 01-财税自动化，行为与改造前一致。"""
    monkeypatch.setattr(svc.settings, "finance_board_pipeline_path", "")
    monkeypatch.setattr(svc.settings, "finance_asset_anchor", "")
    d = svc.pipeline_dir()
    assert d.name == "微信报表监控"
    assert d.parent.name == "01-财税自动化"


def test_pipeline_dir_anchor_not_found(monkeypatch):
    """目录链上找不到锚点时抛中文 PipelineUnavailableError（文案不变）。"""
    monkeypatch.setattr(svc.settings, "finance_board_pipeline_path", "")
    monkeypatch.setattr(svc.settings, "finance_asset_anchor", "不存在的锚点xyz")
    with pytest.raises(svc.PipelineUnavailableError, match="无法定位微信报表监控目录"):
        svc.pipeline_dir()
