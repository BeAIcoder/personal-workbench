# -*- coding: utf-8 -*-
"""技能市场/WorkBuddy 路径配置化测试（分项B）：settings 字段生效、env 优先、全空回落常量。"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.agents import skill_market as sm
from app.agents import workbuddy_sync as wb
from app.config import settings


@pytest.fixture()
def clean_env(monkeypatch):
    """清掉两个 env 变量 + settings 字段 + 界面设置文件，保证解析链从空白开始。"""
    monkeypatch.delenv("QWENPAW_SKILL_POOL", raising=False)
    monkeypatch.delenv("WORKBUDDY_HOME", raising=False)
    monkeypatch.setattr(settings, "qwenpaw_skill_pool", "")
    monkeypatch.setattr(settings, "workbuddy_home", "")
    # 界面设置文件可能真实存在且带 home，隔离为空
    monkeypatch.setattr(wb, "load_settings", lambda: {"home": ""})


# ---------- skill_market._qwenpaw_pool ----------

def test_qwenpaw_pool_settings_field_takes_effect(clean_env, monkeypatch):
    """env 为空时 .env 级 settings.qwenpaw_skill_pool 生效。"""
    monkeypatch.setattr(settings, "qwenpaw_skill_pool", "D:/path/to/env-file/skill_pool")
    assert sm._qwenpaw_pool() == Path("D:/path/to/env-file/skill_pool")


def test_qwenpaw_pool_env_beats_settings(clean_env, monkeypatch):
    """旧 env 变量优先于 settings 字段（旧测试 monkeypatch.setenv 依赖）。"""
    monkeypatch.setattr(settings, "qwenpaw_skill_pool", "D:/path/to/env-file/skill_pool")
    monkeypatch.setenv("QWENPAW_SKILL_POOL", "D:/path/to/runtime-env/skill_pool")
    assert sm._qwenpaw_pool() == Path("D:/path/to/runtime-env/skill_pool")


def test_qwenpaw_pool_falls_back_to_constant(clean_env):
    """env 与 settings 全空时回落 QWENPAW_SKILL_POOL_DEFAULT 常量。"""
    assert sm._qwenpaw_pool() == Path(sm.QWENPAW_SKILL_POOL_DEFAULT)


# ---------- workbuddy_sync._resolve_home ----------

def test_wb_home_settings_field_takes_effect(clean_env, monkeypatch):
    """参数/界面设置/env 全空时 settings.workbuddy_home 生效。"""
    monkeypatch.setattr(settings, "workbuddy_home", "D:/path/to/env-file/.workbuddy")
    assert wb._resolve_home() == Path("D:/path/to/env-file/.workbuddy")


def test_wb_home_env_beats_settings(clean_env, monkeypatch):
    """env WORKBUDDY_HOME 优先于 settings 字段。"""
    monkeypatch.setattr(settings, "workbuddy_home", "D:/path/to/env-file/.workbuddy")
    monkeypatch.setenv("WORKBUDDY_HOME", "D:/path/to/runtime-env/.workbuddy")
    assert wb._resolve_home() == Path("D:/path/to/runtime-env/.workbuddy")


def test_wb_home_ui_settings_beat_env_and_settings(clean_env, monkeypatch):
    """界面设置（load_settings().home）优先于 env 与 settings 字段。"""
    monkeypatch.setattr(wb, "load_settings", lambda: {"home": "D:/path/to/ui/.workbuddy"})
    monkeypatch.setenv("WORKBUDDY_HOME", "D:/path/to/runtime-env/.workbuddy")
    monkeypatch.setattr(settings, "workbuddy_home", "D:/path/to/env-file/.workbuddy")
    assert wb._resolve_home() == Path("D:/path/to/ui/.workbuddy")


def test_wb_home_param_beats_all(clean_env, monkeypatch):
    """显式参数 home 优先级最高。"""
    monkeypatch.setattr(wb, "load_settings", lambda: {"home": "D:/path/to/ui/.workbuddy"})
    monkeypatch.setenv("WORKBUDDY_HOME", "D:/path/to/runtime-env/.workbuddy")
    monkeypatch.setattr(settings, "workbuddy_home", "D:/path/to/env-file/.workbuddy")
    assert wb._resolve_home("D:/path/to/param/.workbuddy") == Path("D:/path/to/param/.workbuddy")


def test_wb_home_falls_back_to_constant(clean_env):
    """参数/界面设置/env/settings 全空时回落 DEFAULT_HOME 常量。"""
    assert wb._resolve_home() == Path(wb.DEFAULT_HOME)
