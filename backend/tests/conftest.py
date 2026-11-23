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
_FINANCE_DIR = _TEMP_ROOT / f"workbench_finance_{os.getpid()}"
_TEST_DATA = _TEMP_ROOT / f"workbench_data_{os.getpid()}"
_CORE_TABLE = "tasks"
# 上一次 client 用例 teardown 已重建好空表：紧邻的下一个 client 用例可跳过重复 DDL
_SCHEMA_PRISTINE = False
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"
os.environ["SEED_ON_STARTUP"] = "0"
# 调试中心数据目录指向临时目录：测试不碰真实 backend/data/debug
os.environ["DEBUG_DATA_DIR"] = str(_DEBUG_DIR)
# 财务模块数据目录同样强制隔离：未显式 monkeypatch 的测试也不得写真实 inbox
os.environ["FINANCE_DATA_DIR"] = str(_FINANCE_DIR)
# 测试环境不读开发者本机 backend/.env：避免 SOFFICE_PATH/QWENPAW_SKILL_POOL 等
# 真实配置改变默认值断言或触达兄弟项目引擎（BUGfix TL-13）
os.environ["WORKBENCH_ENV_FILE"] = ""
# 测试环境不接入模型：显式清空 LLM 配置
for _k in ("LLM_PROVIDER", "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
    os.environ[_k] = ""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from app.database import Base, engine
from app.main import app
from app import config as app_config
from app.agents import expert_packs, skill_store
from app.routers import assistant as assistant_router

# 上传/技能包/专家包目录也指向临时目录（BUGfix TL-12）：
# 未显式 monkeypatch 的用例不得写真实 backend/data。
_TEST_DATA.mkdir(parents=True, exist_ok=True)
app_config.UPLOAD_DIR = _TEST_DATA / "uploads"
assistant_router.UPLOAD_DIR = app_config.UPLOAD_DIR
skill_store.DATA_DIR = _TEST_DATA
expert_packs.DATA_DIR = _TEST_DATA


@pytest.fixture(autouse=True)
def _isolate_database(request):
    """使用 client 的用例每个都从空库开始（BUGfix TL-11）。

    7 个测试文件自行覆写 client 夹具、不走原 client 里的 drop/create，
    会把上一个用例的 memory/mcp/skill 行带进下一个文件。这里按「是否请求
    client（含 sclient 等依赖它的夹具）」统一重置；纯函数/单元测试不做 DDL，
    避免全量回归被无谓拖慢。

    teardown 必须重建空表（T7A）：只 drop 不重建时，排在 client 重文件之后的
    非 client 触库用例会撞「no such table」。重建后置 `_SCHEMA_PRISTINE`，
    紧邻的下一个 client 用例即可跳过重复 DDL，整体 DDL 次数不增。
    """
    global _SCHEMA_PRISTINE
    if "client" not in request.fixturenames:
        # 非 client 用例也可能直接触库；若上一个用例把表 drop 了（自定义 client
        # 夹具、用例内显式清理等），这里先补建，避免按文件顺序或并行分片时无表可用。
        if not _schema_present():
            _rebuild_schema()
        _SCHEMA_PRISTINE = False
        yield
        return
    if not _SCHEMA_PRISTINE:
        _rebuild_schema()
    _SCHEMA_PRISTINE = False
    yield
    _rebuild_schema()
    _SCHEMA_PRISTINE = True


def _rebuild_schema() -> None:
    """drop 后立即重建：用例之间保持「空表」而不是「无表」。"""
    engine.dispose()
    Base.metadata.drop_all(bind=engine)
    _drop_kb_virtual_tables()
    Base.metadata.create_all(bind=engine)
    _ensure_kb_schema()


def _schema_present() -> bool:
    return inspect(engine).has_table(_CORE_TABLE)


def _drop_kb_virtual_tables() -> None:
    """FTS5/vec 虚拟表不在 ORM metadata 里，drop_all 管不到，需显式清理。

    否则上一个用例的索引行会残留到下一个用例，造成「删了文档还能搜到」的假象。
    """
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE IF EXISTS kb_fts")
        conn.exec_driver_sql("DROP TABLE IF EXISTS kb_vec")


def _ensure_kb_schema() -> None:
    from app.knowledge.index import ensure_schema

    ensure_schema()


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def pytest_sessionfinish(session, exitstatus):
    engine.dispose()
    try:
        _TEST_DB.unlink()
    except (FileNotFoundError, PermissionError):
        pass
    shutil.rmtree(_DEBUG_DIR, ignore_errors=True)
    shutil.rmtree(_FINANCE_DIR, ignore_errors=True)
    shutil.rmtree(_TEST_DATA, ignore_errors=True)
