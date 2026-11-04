# -*- coding: utf-8 -*-
"""Task 9：WORKBENCH_DATA_DIR 统一派生数据/上传/默认库。

用独立子进程导入 `app.config`（避免本进程已缓存的模块常量），验证：
- 设置 WORKBENCH_DATA_DIR 后 DATA_DIR/UPLOAD_DIR/默认 DATABASE_URL 同源；
- 未设置时默认仍是 backend/data（保持默认不变）；
- 经 WORKBENCH_ENV_FILE 指向的 .env 也能生效（与其余配置同一入口）；
- 相对路径按 backend/ 解析，不随启动目录漂移。
"""
import json
import os
import subprocess
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PROBE = (
    "import json\n"
    "from app.config import DATA_DIR, UPLOAD_DIR, settings\n"
    "print(json.dumps({"
    "'data': str(DATA_DIR), 'upload': str(UPLOAD_DIR), "
    "'db': settings.database_url}))\n"
)


def _import_config(extra_env: dict) -> dict:
    env = os.environ.copy()
    for key in ("WORKBENCH_DATA_DIR", "WORKBENCH_ENV_FILE", "DATABASE_URL"):
        env.pop(key, None)
    # I7：除 env_file 用例外一律关闭 .env，测试不依赖本机配置、不读真实秘密
    if "WORKBENCH_ENV_FILE" not in extra_env:
        env["WORKBENCH_ENV_FILE"] = ""
    env.update(extra_env)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, "-c", PROBE], cwd=BACKEND, env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


RESTORE_PROBE = (
    "import json, sys\n"
    "sys.path.insert(0, 'scripts')\n"
    "import restore_backup\n"
    "print(json.dumps({'target': str(restore_backup.DEFAULT_TARGET)}))\n"
)


def _import_restore_default(extra_env: dict) -> str:
    env = os.environ.copy()
    for key in ("WORKBENCH_DATA_DIR", "WORKBENCH_ENV_FILE", "DATABASE_URL"):
        env.pop(key, None)
    if "WORKBENCH_ENV_FILE" not in extra_env:
        env["WORKBENCH_ENV_FILE"] = ""
    env.update(extra_env)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, "-c", RESTORE_PROBE], cwd=BACKEND, env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])["target"]


def _posix(path: str) -> str:
    return path.replace("\\", "/")


def test_workbench_data_dir_derives_all_paths(tmp_path):
    root = tmp_path / "isolated-data"
    got = _import_config({"WORKBENCH_DATA_DIR": str(root)})
    assert _posix(got["data"]) == _posix(str(root))
    assert _posix(got["upload"]) == _posix(str(root / "uploads"))
    assert got["db"] == f"sqlite:///{_posix(str(root))}/workbench.db"


def test_default_data_dir_unchanged_without_override():
    got = _import_config({"WORKBENCH_ENV_FILE": ""})
    assert _posix(got["data"]) == _posix(os.path.join(BACKEND, "data"))
    assert _posix(got["upload"]) == _posix(os.path.join(BACKEND, "data", "uploads"))
    assert got["db"].endswith("/backend/data/workbench.db")


def test_workbench_data_dir_can_come_from_env_file(tmp_path):
    root = tmp_path / "env-file-data"
    env_file = tmp_path / "custom.env"
    env_file.write_text(f"WORKBENCH_DATA_DIR={root}\n", encoding="utf-8")
    got = _import_config({
        "WORKBENCH_ENV_FILE": str(env_file),
        "WORKBENCH_DATA_DIR": "",
    })
    assert _posix(got["data"]) == _posix(str(root))


def test_relative_workbench_data_dir_resolves_against_backend(tmp_path):
    got = _import_config({"WORKBENCH_DATA_DIR": "relative-data-dir"})
    assert _posix(got["data"]) == _posix(os.path.join(BACKEND, "relative-data-dir"))
    assert not os.path.exists(os.path.join(BACKEND, "relative-data-dir"))


def test_external_default_paths_are_centralized_in_config():
    """L-036：模块不再各自硬编码本机路径，默认值集中在 config。"""
    from pathlib import Path

    from app import config
    from app.agents import skill_market, workbuddy_sync

    assert workbuddy_sync.DEFAULT_HOME == str(config.DEFAULT_WORKBUDDY_HOME)
    assert skill_market.QWENPAW_SKILL_POOL_DEFAULT == str(config.DEFAULT_QWENPAW_SKILL_POOL)
    assert Path(workbuddy_sync.DEFAULT_HOME) == Path.home() / ".workbuddy"


def test_explicit_data_dir_wins_over_synthetic_env_file(tmp_path):
    """I7：显式 data dir 优先于合成 .env；文件真实设置也不得改变测试路径。"""
    env_file = tmp_path / "would-be-real.env"
    env_file.write_text(f"WORKBENCH_DATA_DIR={tmp_path / 'from-env-file'}\n", encoding="utf-8")
    explicit = tmp_path / "explicit-data"
    got = _import_config({
        "WORKBENCH_DATA_DIR": str(explicit),
        "WORKBENCH_ENV_FILE": str(env_file),
    })
    assert _posix(got["data"]) == _posix(str(explicit))
    assert "from-env-file" not in got["data"]


def test_env_file_last_value_wins_and_inline_comment_stripped(tmp_path):
    """M2：同名末值生效，未加引号的行内注释被剥离；引号内 # 保留。"""
    env_file = tmp_path / "custom.env"
    env_file.write_text(
        f"WORKBENCH_DATA_DIR={tmp_path / 'first'}\n"
        f"WORKBENCH_DATA_DIR={tmp_path / 'last'}  # 行内注释\n",
        encoding="utf-8",
    )
    got = _import_config({"WORKBENCH_ENV_FILE": str(env_file), "WORKBENCH_DATA_DIR": ""})
    assert _posix(got["data"]) == _posix(str(tmp_path / "last"))

    env_file.write_text(
        f'WORKBENCH_DATA_DIR="{tmp_path / "quoted#hash"}"\n', encoding="utf-8")
    got = _import_config({"WORKBENCH_ENV_FILE": str(env_file), "WORKBENCH_DATA_DIR": ""})
    assert _posix(got["data"]) == _posix(str(tmp_path / "quoted#hash"))


def test_restore_default_target_follows_workbench_data_dir(tmp_path):
    """I3：restore 的默认目标必须跟随 WORKBENCH_DATA_DIR，而不是硬编码 backend/data。"""
    root = tmp_path / "restore-default"
    got = _import_restore_default({"WORKBENCH_DATA_DIR": str(root)})
    assert _posix(got) == _posix(str(root))


def test_restore_default_target_unchanged_without_override():
    got = _import_restore_default({"WORKBENCH_ENV_FILE": ""})
    assert _posix(got) == _posix(os.path.join(BACKEND, "data"))
