"""pytest 公共夹具：使用独立的临时 SQLite 数据库，测试之间互不影响、不碰生产数据。"""
import os
import pathlib
import tempfile

_TEST_DB = pathlib.Path(tempfile.gettempdir()) / f"workbench_test_{os.getpid()}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"
os.environ["SEED_ON_STARTUP"] = "0"
# 测试环境不接入模型：显式清空 LLM 配置，避免受开发者本地 backend/.env 影响
for _k in ("LLM_PROVIDER", "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
    os.environ[_k] = ""

import pytest
from fastapi.testclient import TestClient

from app.database import Base, engine
from app.main import app


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)


def pytest_sessionfinish(session, exitstatus):
    engine.dispose()
    try:
        _TEST_DB.unlink()
    except (FileNotFoundError, PermissionError):
        pass
