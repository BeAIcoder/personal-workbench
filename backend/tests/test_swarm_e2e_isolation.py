# -*- coding: utf-8 -*-
"""E2E 脚本隔离守护（swarm 任务 e2e-isolation；Task 9 扩展到 19 个区域探针）。

被守护的契约：
1. dev/e2e 三个旧脚本（e2e_recon / e2e_interactions / e2e_edge）启动服务前必须使用
   各自独立的 DATABASE_URL / FINANCE_DATA_DIR / DEBUG_DATA_DIR；
2. dev/e2e/probes/ 下 19 个区域探针还必须额外设置 WORKBENCH_DATA_DIR，四域
   （数据根/库/财务/调试）彼此独立，路径只能落在 dev/e2e 或 .superpowers，
   绝不指向 backend/data 真实数据（uploads/skills/expert-packs 都挂在数据根下）；
3. 隔离路径全部位于 dev/e2e 下，绝不指向 backend/data 真实数据
   （recon 曾缺失 FINANCE_DATA_DIR，服务会直接写真实财务目录）；
4. 归档器 debug_reporter 读与服务相同的调试目录：DEBUG_DATA_DIR 同时进父进程
   os.environ（归档器在本进程读）与子进程 env（服务读）；
5. 端口沿用 8123/8124；三脚本持同一把运行锁，禁止并行执行；
6. 每次运行前重置本脚本自有的库/财务/调试目录（探针还需重置数据根）。

验证方式：静态解析脚本源码（AST 求值路径字面量，含 env.update 字典）+ 临时目录行为测试。
不启动服务、不跑 Playwright、不读/不删真实 debug 数据。
"""
import ast
import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
E2E_DIR = REPO / "dev" / "e2e"

# 静态求值哨兵：脚本里的 Path(__file__).resolve().parents[2] 统一折成 <REPO>，
# 只比较其后缀结构（/dev/e2e/...），不依赖绝对路径。
_ROOT = "<REPO>"

SCRIPTS = {
    "recon": E2E_DIR / "e2e_recon.py",
    "interactions": E2E_DIR / "e2e_interactions.py",
    "edge": E2E_DIR / "e2e_edge.py",
}
PORTS = {"recon": 8123, "interactions": 8123, "edge": 8124}
ISOLATION_KEYS = ("DATABASE_URL", "FINANCE_DATA_DIR", "DEBUG_DATA_DIR")

_spec = importlib.util.spec_from_file_location(
    "e2e_debug_reporter", E2E_DIR / "debug_reporter.py"
)
debug_reporter = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(debug_reporter)


# ---------- 脚本源码静态求值 ----------

def _eval(node, values):
    """把脚本中的路径/URL 字面量表达式求值为字符串；无法求值返回 None。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return values.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left, right = _eval(node.left, values), _eval(node.right, values)
        if left is None or right is None:
            return None
        return f"{left.rstrip('/')}/{right.lstrip('/')}"
    if isinstance(node, ast.JoinedStr):
        parts = []
        for piece in node.values:
            if isinstance(piece, ast.Constant):
                parts.append(str(piece.value))
            elif isinstance(piece, ast.FormattedValue):
                value = _eval(piece.value, values)
                parts.append("" if value is None else str(value))
            else:
                return None
        return "".join(parts)
    if isinstance(node, ast.Call):
        func = node.func
        if (isinstance(func, ast.Name) and func.id == "Path" and node.args
                and isinstance(node.args[0], ast.Name) and node.args[0].id == "__file__"):
            return _ROOT
        if isinstance(func, ast.Name) and func.id in ("str", "Path") and node.args:
            return _eval(node.args[0], values)
        if isinstance(func, ast.Attribute):
            # .as_posix() / .resolve() 等：透传到接收者
            return _eval(func.value, values)
        return None
    if isinstance(node, ast.Attribute):
        # .parents 等：透传
        return _eval(node.value, values)
    if isinstance(node, ast.Subscript):
        # .parents[2]：透传
        return _eval(node.value, values)
    return None


def _subscript_target(target):
    """env["K"] / os.environ["K"] → (bucket, key)；其它 → (None, None)。"""
    if not isinstance(target, ast.Subscript):
        return None, None
    key_node = target.slice
    if not (isinstance(key_node, ast.Constant) and isinstance(key_node.value, str)):
        return None, None
    base = target.value
    if isinstance(base, ast.Name):
        return base.id, key_node.value
    if (isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name)
            and base.value.id == "os" and base.attr == "environ"):
        return "os.environ", key_node.value
    return None, None


def _config(path: Path) -> dict:
    """静态提取一个 E2E 脚本的隔离配置：端口、env/os.environ 赋值、Popen/锁/重置的行号。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    values = {}
    env, env_lines = {}, {}
    parent_env, parent_env_lines = {}, {}
    port = None
    popen_line = lock_line = None
    resets = {}  # 被重置的路径 → 行号
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                if target.id == "PORT" and isinstance(node.value, ast.Constant):
                    port = node.value.value
                else:
                    values[target.id] = _eval(node.value, values)
                continue
            bucket, key = _subscript_target(target)
            if key is None:
                continue
            value = _eval(node.value, values)
            if bucket == "env":
                env[key], env_lines[key] = value, node.lineno
            else:
                parent_env[key], parent_env_lines[key] = value, node.lineno
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if (name == "update" and isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name) and func.value.id == "env"
                    and node.args and isinstance(node.args[0], ast.Dict)):
                for key_node, value_node in zip(node.args[0].keys, node.args[0].values):
                    if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
                        env[key_node.value] = _eval(value_node, values)
                        env_lines[key_node.value] = node.lineno
                continue
            if name == "Popen" and popen_line is None:
                popen_line = node.lineno
            elif name == "acquire_run_lock" and lock_line is None:
                lock_line = node.lineno
            elif name == "rmtree" and node.args:
                resolved = _eval(node.args[0], values)
                if resolved:
                    resets[resolved] = node.lineno
            elif name == "unlink" and isinstance(func, ast.Attribute):
                resolved = _eval(func.value, values)
                if resolved:
                    resets[resolved] = node.lineno
    return {
        "port": port,
        "env": env,
        "env_lines": env_lines,
        "parent_env": parent_env,
        "parent_env_lines": parent_env_lines,
        "popen_line": popen_line,
        "lock_line": lock_line,
        "resets": resets,
    }


def _isolation_env(cfg: dict, kind: str) -> dict:
    missing = [k for k in ISOLATION_KEYS if k not in cfg["env"]]
    assert not missing, f"{kind} 未在启动服务前设置隔离变量: {missing}（{SCRIPTS[kind]}）"
    env = {k: cfg["env"][k] for k in ISOLATION_KEYS}
    for key, value in env.items():
        assert value, f"{kind} 的 {key} 无法静态求值（写法变了？请同步本测试）"
    return env


# ---------- 隔离契约 ----------

@pytest.mark.parametrize("kind", list(SCRIPTS))
def test_sets_three_isolation_vars_before_server_start(kind):
    cfg = _config(SCRIPTS[kind])
    env = _isolation_env(cfg, kind)
    assert cfg["popen_line"], f"{kind} 未找到 subprocess.Popen（服务启动）"
    for key in ISOLATION_KEYS:
        assert cfg["env_lines"][key] < cfg["popen_line"], \
            f"{kind} 的 {key} 设置于服务启动之后"


@pytest.mark.parametrize("kind", list(SCRIPTS))
def test_isolation_paths_stay_under_dev_e2e(kind):
    cfg = _config(SCRIPTS[kind])
    env = _isolation_env(cfg, kind)
    db_url = env["DATABASE_URL"]
    assert db_url.startswith("sqlite:///"), f"{kind} DATABASE_URL 不是 sqlite: {db_url}"
    paths = {
        "DATABASE_URL": db_url[len("sqlite:///"):],
        "FINANCE_DATA_DIR": env["FINANCE_DATA_DIR"],
        "DEBUG_DATA_DIR": env["DEBUG_DATA_DIR"],
    }
    for key, path in paths.items():
        assert path.startswith(f"{_ROOT}/dev/e2e/"), \
            f"{kind} 的 {key} 不在 dev/e2e 下: {path}"
        assert "backend/data" not in path, \
            f"{kind} 的 {key} 指向 backend/data 真实数据: {path}"
    for key, value in cfg["env"].items():
        assert "backend/data" not in str(value), \
            f"{kind} 的 {key} 指向 backend/data 真实数据: {value}"


@pytest.mark.parametrize("kind", list(SCRIPTS))
def test_archiver_reads_same_debug_dir_as_service(kind):
    cfg = _config(SCRIPTS[kind])
    env = _isolation_env(cfg, kind)
    assert "DEBUG_DATA_DIR" in cfg["parent_env"], (
        f"{kind} 未在父进程设置 DEBUG_DATA_DIR：归档器 debug_reporter 会落到"
        f"默认目录 backend/data/debug（真实调试中心）"
    )
    assert cfg["parent_env"]["DEBUG_DATA_DIR"] == env["DEBUG_DATA_DIR"], \
        f"{kind} 父进程与服务端的 DEBUG_DATA_DIR 不一致"
    assert cfg["parent_env_lines"]["DEBUG_DATA_DIR"] < cfg["popen_line"]


def test_scripts_use_independent_directories():
    envs = {kind: _isolation_env(_config(path), kind) for kind, path in SCRIPTS.items()}
    for key in ISOLATION_KEYS:
        values = {kind: env[key] for kind, env in envs.items()}
        assert len(set(values.values())) == len(SCRIPTS), \
            f"三脚本的 {key} 未各自独立: {values}"


@pytest.mark.parametrize("kind", list(SCRIPTS))
def test_ports_unchanged(kind):
    cfg = _config(SCRIPTS[kind])
    assert cfg["port"] == PORTS[kind], f"{kind} 端口应沿用 {PORTS[kind]}"


@pytest.mark.parametrize("kind", list(SCRIPTS))
def test_run_lock_and_per_run_reset(kind):
    cfg = _config(SCRIPTS[kind])
    assert cfg["lock_line"] is not None, \
        f"{kind} 未获取运行互斥锁（固定端口 8123/8124，禁止并行执行）"
    assert cfg["lock_line"] < cfg["popen_line"], "运行锁应在启动服务前获取"
    env = _isolation_env(cfg, kind)
    targets = {
        "DATABASE_URL": env["DATABASE_URL"][len("sqlite:///"):],
        "FINANCE_DATA_DIR": env["FINANCE_DATA_DIR"],
        "DEBUG_DATA_DIR": env["DEBUG_DATA_DIR"],
    }
    for key, path in targets.items():
        assert path in cfg["resets"], \
            f"{kind} 运行前未重置自有目录 {path}（{key}），每次运行目录不独立"
        assert cfg["resets"][path] < cfg["popen_line"]
    assert cfg["lock_line"] < min(cfg["resets"].values()), "应先拿锁再重置目录"


# ---------- 归档器行为（临时目录） ----------

def test_archiver_writes_into_debug_data_dir(tmp_path, monkeypatch):
    debug_dir = tmp_path / "debug-data"
    monkeypatch.setenv("DEBUG_DATA_DIR", str(debug_dir))
    shots = tmp_path / "shots"
    shots.mkdir()
    (shots / "dashboard.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)

    run_id = debug_reporter.save_run(
        "recon",
        base="http://127.0.0.1:8123",
        started_at="2026-10-22 10:00:00",
        finished_at="2026-10-22 10:01:00",
        pages=[{"route": "/", "name": "dashboard", "status": 200,
                "console_errors": [], "page_errors": [], "failed_reqs": []}],
        tests=[{"id": "TC-1", "name": "创建任务", "passed": False, "detail": "AssertionError: boom"}],
        shots_dir=shots,
    )

    assert debug_reporter._debug_dir() == debug_dir
    result = debug_dir / "runs" / run_id / "result.json"
    assert result.is_file()
    data = json.loads(result.read_text(encoding="utf-8"))
    assert data["kind"] == "recon"
    assert data["totals"]["failed_tests"] == 1
    assert (debug_dir / "runs" / run_id / "shots" / "dashboard.png").is_file()
    assert (debug_dir / "issues.json").is_file()
    # 绝不写真实调试目录
    assert not (REPO / "backend" / "data" / "debug" / "runs" / run_id).exists()


def test_archiver_default_debug_dir_points_at_backend_data(monkeypatch):
    """未设 DEBUG_DATA_DIR 时归档器的默认落点（即旧脚本污染真实调试中心的原因）。"""
    monkeypatch.delenv("DEBUG_DATA_DIR", raising=False)
    assert debug_reporter._debug_dir() == REPO / "backend" / "data" / "debug"


# ---------- 运行互斥锁（临时目录） ----------

def test_run_lock_is_exclusive_and_released(tmp_path):
    release = debug_reporter.acquire_run_lock(tmp_path)
    assert (tmp_path / ".e2e-run.lock").is_file()
    with pytest.raises(RuntimeError):
        debug_reporter.acquire_run_lock(tmp_path)
    release()
    release()  # 幂等
    release2 = debug_reporter.acquire_run_lock(tmp_path)
    release2()


# ---------- 19 个区域探针的四域隔离契约（Task 9 / L-152） ----------

PROBES_DIR = E2E_DIR / "probes"
PROBES = {path.stem: path for path in sorted(PROBES_DIR.glob("*.py"))}
# M6：perf_probe 不在 probes/ 但同样启动服务，必须纳入四域隔离契约
PROBES["perf_probe"] = E2E_DIR / "perf_probe.py"
PROBE_KEYS = ("WORKBENCH_DATA_DIR", "DATABASE_URL", "FINANCE_DATA_DIR", "DEBUG_DATA_DIR")
# 探针产物只允许落在仓库开发目录（dev/e2e 或 .superpowers 的会话产物目录）
ALLOWED_ROOTS = (f"{_ROOT}/dev/e2e/", f"{_ROOT}/.superpowers/")


def _probe_paths(cfg: dict, name: str) -> dict:
    missing = [k for k in PROBE_KEYS if k not in cfg["env"]]
    assert not missing, f"{name} 未设置隔离变量: {missing}（{PROBES[name]}）"
    paths = {}
    for key in PROBE_KEYS:
        value = cfg["env"][key]
        assert value, f"{name} 的 {key} 无法静态求值（写法变了？请同步本测试）"
        paths[key] = value
    assert paths["DATABASE_URL"].startswith("sqlite:///"), \
        f"{name} DATABASE_URL 不是 sqlite: {paths['DATABASE_URL']}"
    paths["DATABASE_URL"] = paths["DATABASE_URL"][len("sqlite:///"):]
    return paths


def _reset_covers(cfg: dict, path: str) -> bool:
    for reset in cfg["resets"]:
        if path == reset or path.startswith(reset.rstrip("/") + "/"):
            return True
    return False


def test_probes_are_all_covered():
    assert len(PROBES) == 20, f"探针数量变了（{len(PROBES)}），请同步四域隔离契约: {sorted(PROBES)}"


@pytest.mark.parametrize("name", sorted(PROBES))
def test_probe_declares_four_isolated_domains(name):
    cfg = _config(PROBES[name])
    paths = _probe_paths(cfg, name)
    assert cfg["popen_line"], f"{name} 未找到服务启动（subprocess.Popen）"
    for key, path in paths.items():
        assert path.startswith(ALLOWED_ROOTS), f"{name} 的 {key} 不在开发目录下: {path}"
        assert "backend/data" not in path, f"{name} 的 {key} 指向真实 backend/data: {path}"
        assert cfg["env_lines"][key] < cfg["popen_line"], \
            f"{name} 的 {key} 设置于服务启动之后"
    # 四个域必须彼此独立：数据根与库/财务/调试目录不能是同一路径
    assert len(set(paths.values())) == len(paths), f"{name} 的隔离路径互相重叠: {paths}"


@pytest.mark.parametrize("name", sorted(PROBES))
def test_probe_resets_own_data_dir_before_start(name):
    cfg = _config(PROBES[name])
    paths = _probe_paths(cfg, name)
    data_dir = paths["WORKBENCH_DATA_DIR"]
    assert _reset_covers(cfg, data_dir), (
        f"{name} 运行前未重置 WORKBENCH_DATA_DIR 目录 {data_dir}："
        f"上传/技能/专家包会跨次残留"
    )
    reset_line = min(
        line for reset, line in cfg["resets"].items()
        if data_dir == reset or data_dir.startswith(reset.rstrip("/") + "/")
    )
    assert reset_line < cfg["popen_line"], "隔离目录应在启动服务前重置"


def test_probe_data_roots_are_unique():
    data_dirs = {}
    for name, path in PROBES.items():
        cfg = _config(path)
        data_dirs[name] = _probe_paths(cfg, name)["WORKBENCH_DATA_DIR"]
    assert len(set(data_dirs.values())) == len(data_dirs), data_dirs
