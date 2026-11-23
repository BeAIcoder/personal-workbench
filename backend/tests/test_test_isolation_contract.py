"""测试隔离契约（Task 7A）：用例之间「空表」而非「无表」，非 client 触库用例必须自建表。

两条独立约束：

1. 运行时：client 类用例的 teardown 之后，共享测试库必须留下可用的空表。
   历史实现只在 teardown 里 `drop_all` 不重建，排在 client 重文件之后的非 client
   触库用例会撞「no such table」。这里用子进程跑「先 client 写行、后非 client 检查」，
   避免依赖同进程内的用例顺序（xdist 会把同一文件的用例分到不同 worker）。
2. 静态：任何直接使用全局 `SessionLocal` / `engine` 的非 client 用例，所属文件必须
   声明一个 autouse 建表夹具（名称不限，体内 `create_all` 即可）。豁免只留给
   「只读 PRAGMA 探针」——它们不建表也不读业务表，加夹具纯属无谓 DDL。
"""
import ast
import os
import pathlib
import subprocess
import sys

TESTS_DIR = pathlib.Path(__file__).resolve().parent
BACKEND_DIR = TESTS_DIR.parent

# 白名单必须真实最小：每项都要给出理由，且必须是「只读 PRAGMA 探针」，
# 用 SessionLocal 的用例一律补夹具，不允许用豁免绕过。
WHITELIST = {
    "test_database_pragmas.py": "只读 PRAGMA 探针（journal_mode/busy_timeout），不建表也不读业务表",
    "test_perf_sqlite_temp_store.py": "只读 PRAGMA 探针（temp_store），业务表访问在同文件的 client 用例",
}

_DB_MODULE = "app.database"
_GLOBAL_DB_NAMES = frozenset({"SessionLocal", "engine"})
_CLIENT_FIXTURES = frozenset({"client", "sclient"})

_INNER_TESTS = '''
from sqlalchemy import inspect

from app.database import SessionLocal, engine
from app.models import Task


def test_a_client_case_writes_row(client):
    resp = client.post("/api/tasks", json={"title": "T7A 隔离探针"})
    assert resp.status_code == 201


def test_b_schema_rebuilt_and_empty_after_teardown():
    assert "tasks" in set(inspect(engine).get_table_names())
    with SessionLocal() as db:
        assert db.query(Task).count() == 0
'''


def _fixture_decorator(node: ast.FunctionDef) -> ast.expr | None:
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
        if name == "fixture":
            return dec
    return None


def _is_autouse_fixture(node: ast.FunctionDef) -> bool:
    dec = _fixture_decorator(node)
    if not isinstance(dec, ast.Call):
        return False
    return any(
        kw.arg == "autouse" and isinstance(kw.value, ast.Constant) and kw.value.value is True
        for kw in dec.keywords
    )


def _creates_tables(node: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Attribute) and n.attr == "create_all" for n in ast.walk(node)
    )


def _called_names(node: ast.AST) -> set[str]:
    return {n.func.id for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}


def _names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _assigned_names(node: ast.AST) -> set[str]:
    """函数体内被本作用域重新绑定的名字——本地 `engine = create_engine(...)` 不算全局库。"""
    out: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Assign):
            targets = n.targets
        elif isinstance(n, (ast.AnnAssign, ast.AugAssign)):
            targets = [n.target]
        elif isinstance(n, (ast.For, ast.AsyncFor, ast.comprehension)):
            targets = [n.target]
        elif isinstance(n, ast.withitem) and n.optional_vars is not None:
            targets = [n.optional_vars]
        else:
            continue
        for target in targets:
            out |= {x.id for x in ast.walk(target) if isinstance(x, ast.Name)}
    return out


def _global_db_aliases(tree: ast.Module) -> set[str]:
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == _DB_MODULE:
            for alias in node.names:
                if alias.name in _GLOBAL_DB_NAMES:
                    aliases.add(alias.asname or alias.name)
    return aliases


def _scan_source(source: str) -> list[str]:
    """返回违反契约的用例名（空列表=合规）；合成源码可直接断言。"""
    tree = ast.parse(source)
    db_aliases = _global_db_aliases(tree)
    funcs = {
        n.name: n
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    module_covered = any(
        _is_autouse_fixture(node) and _creates_tables(node)
        for name, node in funcs.items()
        if not name.startswith("test")
    )

    def closure(start: str) -> set[str]:
        seen: set[str] = set()
        stack = [start]
        while stack:
            cur = stack.pop()
            if cur in seen or cur not in funcs:
                continue
            seen.add(cur)
            stack.extend(_called_names(funcs[cur]))
        return seen

    offenders: list[str] = []
    for name, node in funcs.items():
        if not name.startswith("test"):
            continue
        reachable = closure(name)
        used = set()
        assigned = set()
        for func_name in reachable:
            used |= _names(funcs[func_name]) & db_aliases
            assigned |= _assigned_names(funcs[func_name])
        if not used - assigned:
            continue

        fixtures: set[str] = set()
        stack = [a.arg for a in node.args.args if a.arg not in ("self", "cls")]
        while stack:
            cur = stack.pop()
            if cur in fixtures:
                continue
            fixtures.add(cur)
            if cur in funcs:
                stack.extend(a.arg for a in funcs[cur].args.args if a.arg not in ("self", "cls"))
        if fixtures & _CLIENT_FIXTURES:
            continue
        if any(_creates_tables(funcs[f]) for f in reachable):
            continue
        if module_covered:
            continue
        offenders.append(name)
    return sorted(offenders)


def test_client_teardown_leaves_rebuilt_empty_schema(tmp_path):
    """子进程跑「client 写行 → 非 client 检查」：teardown 必须重建空表。"""
    (tmp_path / "conftest.py").write_text(
        (TESTS_DIR / "conftest.py").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "test_isolation_probe.py").write_text(_INNER_TESTS, encoding="utf-8")
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    env = dict(
        os.environ,
        PYTHONPATH=str(BACKEND_DIR),
        PYTHONIOENCODING="utf-8",
        TEMP=str(temp_root),
        TMP=str(temp_root),
    )
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(tmp_path)],
        cwd=str(BACKEND_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-2000:]


def test_non_client_db_tests_declare_table_fixture():
    """非 client 用例直接用全局 SessionLocal/engine 时，文件必须有 autouse 建表夹具。"""
    offenders: dict[str, list[str]] = {}
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        if path.name in WHITELIST:
            continue
        found = _scan_source(path.read_text(encoding="utf-8"))
        if found:
            offenders[path.name] = found
    assert not offenders, (
        "以下非 client 用例直接使用全局库却缺少 autouse 建表夹具（补 `_ensure_tables`："
        f"`Base.metadata.create_all(bind=engine)`）：{offenders}"
    )


def test_whitelist_is_minimal_and_justified():
    """白名单只放「确实违规但只读 PRAGMA」的文件，且不可借此夹带写库用例。"""
    assert len(WHITELIST) <= 2, f"白名单膨胀到 {len(WHITELIST)} 项，先证明新项为何不能补夹具"
    for name, reason in WHITELIST.items():
        path = TESTS_DIR / name
        assert path.is_file(), f"白名单文件不存在：{name}"
        source = path.read_text(encoding="utf-8")
        assert reason.strip(), f"白名单缺少理由：{name}"
        assert "SessionLocal" not in source, f"{name} 使用了 SessionLocal，应补夹具而不是豁免"
        assert _scan_source(source), f"{name} 已不触发契约，白名单项应删除"


def test_scanner_flags_missing_and_recognizes_covered():
    """元测试：扫描器本身要能区分「漏夹具」与三种合规写法，避免契约静默失效。"""
    missing = _scan_source(
        "from app.database import SessionLocal\n"
        "\n"
        "def test_uses_global_session():\n"
        "    with SessionLocal() as db:\n"
        "        db.execute(None)\n"
    )
    assert missing == ["test_uses_global_session"]

    covered = (
        "import pytest\n"
        "from app.database import Base, SessionLocal, engine\n"
        "\n"
        "@pytest.fixture(autouse=True)\n"
        "def _ensure_tables():\n"
        "    Base.metadata.create_all(bind=engine)\n"
        "    yield\n"
        "\n"
        "def test_uses_global_session():\n"
        "    with SessionLocal() as db:\n"
        "        db.execute(None)\n"
    )
    assert _scan_source(covered) == []

    client_covered = (
        "from app.database import SessionLocal\n"
        "\n"
        "def test_uses_global_session(client):\n"
        "    with SessionLocal() as db:\n"
        "        db.execute(None)\n"
    )
    assert _scan_source(client_covered) == []

    local_engine = (
        "from sqlalchemy import create_engine\n"
        "from app.database import Base\n"
        "\n"
        "def test_builds_own_engine(tmp_path):\n"
        "    engine = create_engine('sqlite://')\n"
        "    Base.metadata.create_all(bind=engine)\n"
    )
    assert _scan_source(local_engine) == []
