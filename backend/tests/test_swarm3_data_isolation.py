# -*- coding: utf-8 -*-
"""测试进程的数据目录隔离守护：pytest 不得默认写真实 backend/data/finance。"""
from pathlib import Path

from app.config import get_finance_data_dir, settings


def test_finance_data_dir_is_isolated_from_real_backend_data():
    configured = settings.finance_data_dir
    path = Path(configured) if configured else get_finance_data_dir()
    real = Path(__file__).resolve().parents[1] / "data"
    assert path.resolve() != real.resolve()
    assert not str(path.resolve()).startswith(str(real.resolve())), \
        "测试未隔离 FINANCE_DATA_DIR，会写真实财务目录"
