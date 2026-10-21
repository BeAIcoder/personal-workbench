"""pytest 公共夹具：使用独立的临时 SQLite 数据库，测试之间互不影响、不碰生产数据。"""
import os
import pathlib
import shutil
import tempfile

_REPO_BACKEND = pathlib.Path(__file__).resolve().parent.parent


def _temp_root() -> pathlib.Path:
    """选一个真实可写的系统临时目录。

    Git Bash 会把 TEMP/TMP 设成 POSIX 的 /tmp，Windows 版 Python 无法使用，
    tempfile.gettempdir() 会退化成当前工作目录（曾把测试库/调试目录写进 backend/）。
    因此显式校验候选目录必须是「存在且可写的 Windows 绝对路径」，最后兜底仓库内 .tmp。
    """
    candidates = [
        os.environ.get("TEMP"),
        os.environ.get("TMP"),
        str(pathlib.Path(os.environ.get("LOCALAPPDATA", "")) / "Temp") if os.environ.get("LOCALAPPDATA") else "",
    ]
    fallback = _REPO_BACKEND / ".pytest_tmp"
    for candidate in candidates:
        if not candidate:
            continue
        path = pathlib.Path(candidate)
        if not path.is_absolute() or not path.is_dir():
            continue
        try:
            probe = path / f".workbench_write_probe_{os.getpid()}"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return path
        except OSError:
            continue
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


_TEMP_ROOT = _temp_root()
# stdlib tempfile 的落点：Git Bash 把 TEMP/TMP 设成 POSIX /tmp，Windows Python 用不了，
# gettempdir() 会退化成「当前工作目录」，于是被测代码里的 NamedTemporaryFile 一律把散件
# 建在 backend/（实测留下 0 字节 tmpXXXXXXXX）。这里显式钉到已验证可写的系统临时根。
tempfile.tempdir = str(_TEMP_ROOT)
_TEST_DB = _TEMP_ROOT / f"workbench_test_{os.getpid()}.db"
_DEBUG_DIR = _TEMP_ROOT / f"workbench_debug_{os.getpid()}"
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"
os.environ["SEED_ON_STARTUP"] = "0"
# 调试中心数据目录指向临时目录：测试不碰真实 backend/data/debug
os.environ["DEBUG_DATA_DIR"] = str(_DEBUG_DIR)
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
    shutil.rmtree(_DEBUG_DIR, ignore_errors=True)
