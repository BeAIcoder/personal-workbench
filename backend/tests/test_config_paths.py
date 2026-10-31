"""配置路径基础层测试：utils.find_sibling_project 与 Settings 新增字段默认值。

本文件不依赖数据库：只测纯函数与配置默认值，不引入 client/db fixture。
"""
from app.config import settings
from app.utils import find_sibling_project


def test_find_sibling_project_hits_anchor(tmp_path):
    """tmp_path 下构造「锚点目录/多级项目名」结构，锚点内的深层 start 可命中。"""
    anchor = tmp_path / settings.finance_asset_anchor
    project = anchor / "现金流滚动预测" / "费用分类汇总系统-v2.0"
    project.mkdir(parents=True)
    # start 取项目内一个尚未创建的深层路径：resolve 后仍沿 parents 链向上
    start = project / "scripts" / "entry.py"

    found = find_sibling_project("现金流滚动预测/费用分类汇总系统-v2.0", start)

    assert found == project


def test_find_sibling_project_missing_anchor_returns_none(tmp_path, monkeypatch):
    """目录链上不存在锚点目录时返回 None，不抛异常。"""
    orphan = tmp_path / "orphan" / "deep"
    orphan.mkdir(parents=True)
    # 钉一个目录链上任何层级都不存在的锚点名：tmp_path 可能落在真实锚点目录
    # （如 01-财税自动化）内部，用默认锚点名无法构造「找不到」的场景
    monkeypatch.setattr(settings, "finance_asset_anchor", "不存在的锚点目录xyz")

    assert find_sibling_project("微信报表监控", orphan) is None


def test_find_sibling_project_uses_monkeypatched_anchor(tmp_path, monkeypatch):
    """settings.finance_asset_anchor 被改写后按新锚点查找，原锚点目录不再命中。"""
    new_anchor = tmp_path / "fake_asset_anchor"
    (new_anchor / "微信报表监控").mkdir(parents=True)
    start = new_anchor / "微信报表监控" / "scripts" / "m01.py"
    # 默认锚点名的目录同时存在（含环境里的真实锚点目录），按新锚点语义不得命中
    default_named = tmp_path / "01-财税自动化"
    (default_named / "微信报表监控").mkdir(parents=True)

    monkeypatch.setattr(settings, "finance_asset_anchor", "fake_asset_anchor")

    assert find_sibling_project("微信报表监控", start) == new_anchor / "微信报表监控"
    assert find_sibling_project("微信报表监控", default_named / "微信报表监控" / "m01.py") is None


def test_settings_new_field_defaults():
    """新增配置字段默认值：留空字段为空串，任务名/锚点取内置默认（不改变现有行为）。"""
    assert settings.qwenpaw_skill_pool == ""
    assert settings.workbuddy_home == ""
    assert settings.soffice_path == ""
    assert settings.pdf_font_path == ""
    assert settings.finance_board_task_name == "微信报表监控"
    assert settings.finance_asset_anchor == "01-财税自动化"
