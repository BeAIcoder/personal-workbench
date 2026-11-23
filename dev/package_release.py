"""个人工作台分发包制作脚本（ZIP）。

用法：
    python dev/package_release.py                 # 完整流程：导出→构建→扫描→打包→冒烟
    python dev/package_release.py --skip-smoke    # 跳过解压冒烟
    python dev/package_release.py --keep-staging # 保留中间产物便于排查
    python dev/package_release.py --out <路径>    # 自定义 ZIP 输出路径

流程：
    1. 预检：git 工作区干净、版本四方一致（config.py 单一源）、脱敏映射表可解析且
       非空、node/npm/venv 可用；随后调用 check_pii.py 扫描 HEAD 提交树
       （--archive + 本地词表，只输出路径/规则/位置，不打印命中值）
    2. 导出：git archive HEAD（经 clean 过滤器脱敏后的当前树，绝不带历史提交）
       → 解包到 build/release/personal-workbench-v<版本>-<日期>/
    3. 剔除内部/过程文档（AGENTS.md、各 dated 报告、20261012New/ 等），
       README 去掉 progress.md 文档行（该文件不入库、包里不存在，避免悬空引用）
    4. 数据骨架：backend/data、uploads、finance 只留空目录（.gitkeep）
    5. 前端重建：staging/frontend 内 npm ci + npm run build
       （用脱敏后的源码构建，产物天然脱敏；postbuild 写入内容哈希印章
       dist/.build-stamp.json，收件人端 needs-build 判 UP_TO_DATE，无需 Node 重建）
    6. 敏感串扫描（任一硬失败即中止、不产包）：
       - 脱敏映射表全部真实值逐条反查包内文本文件（含 dist 产物）
       - 全部随包 xlsx 模板按单元格扫描（文本文件覆盖不到共享字符串）
       - 密钥/隐私正则（合成值上下文降级为警告）
       - 结构红线：无 db/上传件/__pycache__/node_modules/venv/.env 等
    7. 打包：zipfile DEFLATED → backups/releases/个人工作台-v<版本>-<日期>.zip
    8. 解压冒烟：解压到 build/smoke/<包名>/，用仓库 venv 以 cwd=解压包/backend
       起 uvicorn（空闲端口），校验 / 200 且含应用挂载点、/docs 200、
       解压包内自动生成 workbench.db；全程只写解压目录，真实 backend/data 零接触

设计约束：
    - 绝不能用 git clone/bundle 代替 archive：过滤器 2026-10-13 才引入，
      更早的历史提交未脱敏，只有当前树（HEAD blob）是可信的脱敏内容。
    - 打包过程对真实仓库只读，不删除、不修改任何用户数据。
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEV = ROOT / "dev"
CONFIG_PY = ROOT / "backend" / "app" / "config.py"
CHECK_PII = DEV / "check_pii.py"
PII_TERMS = DEV / "pii-terms.local.txt"
SANITIZE_MAP = DEV / "sanitize-map.local.json"
BUILD_DIR = ROOT / "build"
STAGING_ROOT = BUILD_DIR / "release"
SMOKE_ROOT = BUILD_DIR / "smoke"
RELEASES_DIR = ROOT / "backups" / "releases"
VENV_PY = ROOT / "backend" / "venv" / "Scripts" / "python.exe"
# PII 门禁使用仓库 venv 的解释器（openpyxl 解析 xlsx 是硬依赖）
PII_PYTHON = VENV_PY

# 包内保留的根目录 Markdown（其余根级 .md 一律视为内部/过程文档剔除）
KEEP_ROOT_MD = {
    "README.md",
    "使用说明.md",
    "数据收传使用说明.md",
    "算法实现逻辑.md",
    "算法和性能优化.md",
    "功能模块拓扑图.md",
    "数据来源与模块数据流.md",
    "注意事项.md",
    "spec.md",
    "API.md",
    "Agent团队设计.md",
}
# 整目录剔除（内部/过程内容）
EXCLUDE_DIRS = {"20261012New"}
# 递归剔除的内部目录（相对仓库根）：测试与脱敏工具会带出真实样本/本机路径，
# 分发包只需要运行时与用户文档；保留 backend/scripts（备份等运维脚本）。
# docs/ 是内部评审截图（v1.1.0 旧界面），既不参与文本扫描也不该随包。
PRUNE_DIRS = (
    "dev",
    "backend/tests",
    "frontend/tests",
    "docs",
)
# 真实客户/员工名称词表：从本地词表/映射表/环境变量加载，真实值不入库
# （本文件随 dev/ 一起被剔除，不参与分发）
from pii_terms import load_pii_terms

# 二进制扩展名（不参与文本扫描；xlsx 另行按单元格扫描）
BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".bmp",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".mp3", ".mp4", ".wav", ".zip", ".gz", ".pdf",
    ".db", ".sqlite", ".sqlite3", ".duckdb", ".xlsx", ".xls",
    ".exe", ".node", ".wasm", ".bin", ".so", ".dll", ".dylib",
}
# 随包模板需要按单元格扫描（映射表真实值/名称常藏在共享字符串里）
XLSX_EXTS = {".xlsx", ".xlsm"}

# 密钥/隐私正则（命中且上下文无合成标记 → 硬失败）
SECRET_PATTERNS = [
    (r"sk-[A-Za-z0-9]{16,}", "OpenAI 风格密钥"),
    (r"AKID[A-Za-z0-9]{10,}", "腾讯云 SecretId"),
    (r"ghp_[A-Za-z0-9]{20,}", "GitHub Token"),
    (r"github_pat_[A-Za-z0-9_]{20,}", "GitHub fine-grained Token"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "私钥"),
    (r"Bearer\s+[A-Za-z0-9\-._~+/]{16,}=*", "Bearer 令牌"),
]
# 上下文出现这些标记视为合成/示例值（测试夹具、文档示例），降级为警告
SYNTHETIC_MARKERS = re.compile(
    r"(?i)(test|fake|dummy|example|sample|synthetic|mock|placeholder|示例|测试|占位|合成)"
)

REQUIRED_FILES = [
    "启动工作台.bat",
    "停止服务.bat",
    "README.md",
    "使用说明.md",
    "数据收传使用说明.md",
    "算法实现逻辑.md",
    "功能模块拓扑图.md",
    "LICENSE",
    "backend/requirements.txt",
    "backend/.env.example",
    "backend/app/main.py",
    "frontend/dist/index.html",
]

# 允许随包的二进制（相对仓库根的 fnmatch；其余一律视为未登记并中止）
ALLOWED_BINARY_PATTERNS = (
    "数据模板/*.xlsx",
    "backend/app/modules/finance_bankflow/template_skeleton.xlsx",
    "frontend/dist/*",  # 构建产物（字体/图标）由构建流程决定
)
# 本机敏感配置：只进本机备份，绝不进分发包
FORBIDDEN_FILE_NAMES = {
    ".env",
    "config.local.yaml",
    "sanitize-map.local.json",
    "pii-terms.local.txt",
}
# requirements.txt 必须能装出运行所需的核心依赖（冒烟用仓库 venv 不会暴露漏项）
REQUIRED_REQUIREMENTS = ("fastapi", "uvicorn", "sqlalchemy", "pydantic")


class PackageError(RuntimeError):
    """打包流程硬失败（预检/构建/扫描/冒烟）。"""


def log(msg: str) -> None:
    print(msg, flush=True)


def run(cmd: list[str], *, cwd: Path, timeout: int = 900, what: str) -> subprocess.CompletedProcess:
    log(f"  $ {' '.join(cmd)}")
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd), timeout=timeout, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
    except FileNotFoundError as exc:
        raise PackageError(f"{what} 失败：找不到命令 {cmd[0]}（{exc}）") from exc
    except subprocess.TimeoutExpired as exc:
        raise PackageError(f"{what} 超时（>{timeout}s）") from exc
    if proc.returncode != 0:
        tail = (proc.stdout or "")[-3000:] + "\n" + (proc.stderr or "")[-3000:]
        raise PackageError(f"{what} 失败（退出码 {proc.returncode}）：\n{tail}")
    return proc


VERSION_PATTERN = re.compile(r'^\s*version:\s*str\s*=\s*"([^"]+)"', re.MULTILINE)


def read_version() -> str:
    """版本唯一来源：backend/app/config.py 的 Settings.version。"""
    try:
        text = CONFIG_PY.read_text(encoding="utf-8")
    except OSError as exc:
        raise PackageError(f"无法读取版本单一源 {CONFIG_PY}：{exc}") from exc
    match = VERSION_PATTERN.search(text)
    if not match:
        raise PackageError(f"版本未在 {CONFIG_PY} 的 Settings.version 中定义，无法确定发布版本")
    return match.group(1)


def _read_json(path: Path, what: str):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PackageError(f"无法读取{what} {path}：{exc}") from exc
    except json.JSONDecodeError as exc:
        raise PackageError(f"{what}不是合法 JSON：{path}（第 {exc.lineno} 行）") from exc


def verify_frontend_version(version: str) -> None:
    """前端 package/package-lock 必须与 config.py 单一源一致（npm ci 可复现构建）。"""
    package = _read_json(ROOT / "frontend" / "package.json", "前端 package.json")
    lock = _read_json(ROOT / "frontend" / "package-lock.json", "前端 package-lock.json")
    lock_root = (lock.get("packages") or {}).get("") or {}
    for label, actual in (
        ("frontend/package.json", str(package.get("version") or "")),
        ("frontend/package-lock.json 顶层", str(lock.get("version") or "")),
        ("frontend/package-lock.json packages['']", str(lock_root.get("version") or "")),
    ):
        if actual != version:
            raise PackageError(
                f"版本不一致：backend/app/config.py={version}，{label}={actual or '缺失'}；"
                "请先统一前后端版本（config.py 为单一源）再打包"
            )


# ---------------------------------------------------------------- 预检

def resolve_tool(name: str) -> str:
    """解析 node/npm：Windows 上需经 PATHEXT 找到 npm.cmd 才能被 CreateProcess 执行。"""
    for candidate in (name, name + ".cmd", name + ".exe", name + ".bat"):
        found = shutil.which(candidate)
        if found:
            return found
    raise PackageError(f"未找到 {name}（前端构建需要；请确认 Node.js 24+ 已安装并在 PATH 中）")


def git_status() -> str:
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if status.returncode != 0:
        raise PackageError("git status 执行失败，请确认在 git 仓库内运行")
    return status.stdout.strip()


def load_sanitize_mapping(path: Path | None = None, *, allow_empty: bool = False) -> list[str]:
    """读取脱敏映射表的真实值；错误信息只说明结构问题，绝不回显真实值。"""
    mapping_path = Path(path) if path is not None else SANITIZE_MAP
    try:
        raw = mapping_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PackageError(
            f"缺少或无法读取脱敏映射表 {mapping_path}（敏感串反查的钥匙）：{exc}"
        ) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PackageError(
            f"脱敏映射表不是合法 JSON，无法保证脱敏质量：{mapping_path}（第 {exc.lineno} 行）"
        ) from exc
    entries = data.get("mappings") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise PackageError(f"脱敏映射表缺少 mappings 数组，无法保证脱敏质量：{mapping_path}")
    reals = [
        str(entry.get("real", ""))
        for entry in entries
        if isinstance(entry, dict) and len(str(entry.get("real", ""))) >= 2
    ]
    if not reals and not allow_empty:
        raise PackageError(
            f"脱敏映射表为空（mappings 无有效真实值），无法反查敏感串：{mapping_path}"
        )
    return reals


PII_GATE_TIMEOUT = 1800


def pii_gate_command(archive: str = "HEAD") -> list[str]:
    command = [str(PII_PYTHON), str(CHECK_PII), "--archive", archive]
    if PII_TERMS.is_file():
        command.extend(["--terms", str(PII_TERMS)])
    command.append("--json")
    return command


def _parse_pii_findings(stdout: str) -> list[dict[str, str]]:
    """只保留 path/rule/location；即使上游误带命中值也不会进入日志或异常。"""
    try:
        data = json.loads(stdout or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    findings: list[dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        findings.append({
            "path": str(item.get("path") or "?"),
            "rule": str(item.get("rule") or "?"),
            "location": str(item.get("location") or "?"),
        })
    return findings


def run_pii_gate(archive: str = "HEAD", *, runner=None) -> None:
    """发布前扫描 HEAD 提交树；命中只报告路径/规则/位置，不打印命中值。"""
    if not CHECK_PII.is_file():
        raise PackageError(f"缺少 PII 门禁脚本 {CHECK_PII}，无法在发布前扫描提交树")
    command = pii_gate_command(archive)
    scope = f"--archive {archive}" + ("（含本地词表）" if str(PII_TERMS) in command else "")
    log(f"      PII 门禁：{scope}")
    execute = runner or subprocess.run
    try:
        proc = execute(
            command, cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=PII_GATE_TIMEOUT,
        )
    except FileNotFoundError as exc:
        raise PackageError(f"PII 门禁执行失败：找不到解释器 {PII_PYTHON}") from exc
    except subprocess.TimeoutExpired as exc:
        raise PackageError(f"PII 门禁超时（>{PII_GATE_TIMEOUT}s），请缩小扫描范围后重试") from exc
    if proc.returncode == 0:
        log("      PII 门禁通过（只报告路径/规则/位置，不含命中值）")
        return
    if proc.returncode != 1:
        raise PackageError(
            f"PII 扫描失败（退出码 {proc.returncode}）；请先运行 "
            f"python dev/check_pii.py --archive {archive} 排查扫描环境"
        )
    findings = _parse_pii_findings(proc.stdout or "")
    if not findings:
        raise PackageError(
            f"PII 门禁未通过，但命中清单无法解析；请运行 "
            f"python dev/check_pii.py --archive {archive} --json 查看（输出不含命中值）"
        )
    lines = [f"{item['path']}:{item['location']} {item['rule']}" for item in findings[:20]]
    more = "" if len(findings) <= 20 else f"\n  …另有 {len(findings) - 20} 处（未展开）"
    raise PackageError(
        f"PII 门禁未通过：命中 {len(findings)} 处（只列路径/规则/位置，不含命中值）：\n  "
        + "\n  ".join(lines) + more
    )


def preflight() -> None:
    log("[1/8] 预检…")
    dirty = git_status()
    if dirty:
        raise PackageError(
            "git 工作区有未提交改动，archive 会漏掉它们。请先提交后再打包：\n" + dirty
        )
    version = read_version()
    verify_frontend_version(version)
    reals = load_sanitize_mapping()
    if not VENV_PY.exists():
        raise PackageError(f"缺少仓库 venv（{VENV_PY}），冒烟验证需要它；确认无误可加 --skip-smoke")
    node = resolve_tool("node")
    npm = resolve_tool("npm")
    log(
        f"      git 干净 / 版本 v{version} 四方一致 / 映射表 {len(reals)} 条真实值 / "
        f"venv 在位 / node={node} / npm={npm}"
    )
    run_pii_gate()


# ---------------------------------------------------------------- 导出与整理

def export_archive(staging: Path) -> int:
    log("[2/8] 导出 git archive HEAD（脱敏后的当前树，不含历史提交）…")
    proc = subprocess.run(
        ["git", "archive", "HEAD"], cwd=str(ROOT), capture_output=True, timeout=300,
    )
    if proc.returncode != 0:
        raise PackageError(f"git archive 失败：{proc.stderr.decode('utf-8', 'replace')[-2000:]}")
    buf = io.BytesIO(proc.stdout)
    with tarfile.open(fileobj=buf, mode="r:") as tar:
        try:
            tar.extractall(path=str(staging), filter="data")
        except TypeError:  # Python < 3.12 无 filter 参数
            tar.extractall(path=str(staging))
    count = sum(len(files) for _, _, files in os.walk(staging))
    log(f"      解包完成：{count} 个文件 → {staging}")
    return count


def prune_internal(staging: Path) -> list[str]:
    log("[3/8] 剔除内部/过程文档，建立数据骨架…")
    removed: list[str] = []
    for entry in sorted(staging.iterdir()):
        if entry.is_dir() and entry.name in EXCLUDE_DIRS:
            removed.append(entry.name + "/")
            shutil.rmtree(entry)
        elif entry.is_file() and entry.suffix == ".md" and entry.name not in KEEP_ROOT_MD:
            removed.append(entry.name)
            entry.unlink()
    # 递归剔除内部目录：dev/ 含脱敏映射与真实样本、tests/ 含真实名称与路径
    for rel in PRUNE_DIRS:
        target = staging / rel
        if target.is_dir():
            removed.append(rel + "/")
            shutil.rmtree(target)
    readme = staging / "README.md"
    if readme.exists():
        text = readme.read_text(encoding="utf-8")
        lines = [ln for ln in text.splitlines() if not ln.startswith("| `progress.md`")]
        newline = "\r\n" if "\r\n" in text else "\n"
        readme.write_text(newline.join(lines) + newline, encoding="utf-8")
        removed.append("README.md:progress.md 行")
    for sub in ("", "uploads", "finance"):
        d = staging / "backend" / "data" / sub if sub else staging / "backend" / "data"
        d.mkdir(parents=True, exist_ok=True)
        keep = d / ".gitkeep"
        if not any(p.name != ".gitkeep" for p in d.iterdir()):
            keep.write_text("", encoding="utf-8")
    log(f"      剔除 {len(removed)} 项；backend/data 只留空目录骨架")
    return removed


# ---------------------------------------------------------------- 前端重建

def build_frontend(staging: Path, npm: str) -> None:
    log("[4/8] 用脱敏后的源码重建前端产物…")
    frontend = staging / "frontend"
    if not (frontend / "package-lock.json").exists():
        raise PackageError("staging 内缺少 frontend/package-lock.json，无法可复现构建")
    run([npm, "ci", "--no-audit", "--no-fund"], cwd=frontend, what="npm ci")
    run([npm, "run", "build"], cwd=frontend, what="npm run build")
    dist = frontend / "dist"
    if not (dist / "index.html").exists() or not any((dist / "assets").glob("*.js")):
        raise PackageError("前端构建后未得到 dist/index.html 或 assets/*.js")
    shutil.rmtree(frontend / "node_modules", ignore_errors=True)
    log("      dist 就绪（含内容哈希印章，收件人端 needs-build 判 UP_TO_DATE，无需 Node 重建）；构建依赖 node_modules 已移除，不进包")


# ---------------------------------------------------------------- 扫描

def iter_text_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() in BINARY_EXTS:
            continue
        yield path


def read_text_strict(path: Path) -> str | None:
    """读取用于扫描的文本：UTF-8 → GB18030 → UTF-16 依次尝试。

    BUGfix TL-02：原实现严格 UTF-8，解码失败即当二进制跳过，导致 GBK 编码的
    bat/vbs（含本机绝对路径）完全不参与敏感串扫描。这里按常见中文编码回退；
    全部失败才视为二进制跳过。
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    for encoding in ("utf-8", "gb18030", "utf-16"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return None


def iter_xlsx_files(root: Path):
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in XLSX_EXTS:
            yield path


def iter_xlsx_cells(path: Path):
    """按单元格产出 (位置, 文本)；损坏或缺少 openpyxl 时硬失败（不静默跳过）。"""
    try:
        import openpyxl

        workbook = openpyxl.load_workbook(path, read_only=True, data_only=False)
    except Exception as exc:  # noqa: BLE001 扫描不能 fail-open
        raise PackageError(f"无法解析 xlsx（文件损坏或缺少 openpyxl）：{path}") from exc
    try:
        for sheet in workbook.worksheets:
            for row in sheet.iter_rows():
                for cell in row:
                    value = cell.value
                    if isinstance(value, str) and value:
                        yield f"{sheet.title}!{cell.coordinate}", value
    except Exception as exc:  # noqa: BLE001
        raise PackageError(f"无法读取 xlsx 单元格（文件损坏）：{path}") from exc
    finally:
        workbook.close()


def scan_secrets(staging: Path) -> None:
    log("[5/8] 敏感串扫描（映射表反查 + 密钥正则 + 结构红线）…")
    warnings: list[str] = []

    reals = load_sanitize_mapping(allow_empty=True)
    log(f"      脱敏映射表 {len(reals)} 条真实值参与反查")

    compiled = [(re.compile(p), label) for p, label in SECRET_PATTERNS]
    map_hits: list[str] = []
    pii_hits: list[str] = []
    regex_fails: list[str] = []
    seen_map: set[tuple[str, int]] = set()
    seen_pii: set[tuple[str, int]] = set()
    pii_terms = load_pii_terms()
    if pii_terms:
        log(f"      真实客户/员工名称 {len(pii_terms)} 条参与反查")
    else:
        warnings.append(
            "PII 词表为空：未做客户/员工名称反查（建议在 dev/pii-terms.local.txt 配置，"
            "或在 dev/sanitize-map.local.json 中补齐）"
        )

    def inspect(label: str, content: str) -> None:
        for index, real in enumerate(reals):
            if real in content and (label, index) not in seen_map:
                seen_map.add((label, index))
                map_hits.append(f"{label} 含映射表真实值（长度 {len(real)}）")
        for index, term in enumerate(pii_terms):
            if term in content and (label, index) not in seen_pii:
                seen_pii.add((label, index))
                pii_hits.append(f"{label} 含真实名称（共 {len(term)} 字）")
        for pattern, secret_label in compiled:
            for match in pattern.finditer(content):
                ctx = content[max(0, match.start() - 80): match.end() + 80]
                line_no = content.count("\n", 0, match.start()) + 1
                item = f"{label} 命中 {secret_label}（第 {line_no} 行）"
                if SYNTHETIC_MARKERS.search(ctx):
                    warnings.append(item + "（上下文为合成/示例值，仅警告）")
                else:
                    regex_fails.append(item)

    for path in iter_text_files(staging):
        rel = path.relative_to(staging).as_posix()
        content = read_text_strict(path)
        if content is None:
            continue
        inspect(rel, content)

    xlsx_files = 0
    xlsx_cells = 0
    for path in iter_xlsx_files(staging):
        rel = path.relative_to(staging).as_posix()
        for location, value in iter_xlsx_cells(path):
            inspect(f"{rel}!{location}", value)
            xlsx_cells += 1
        xlsx_files += 1

    if pii_hits:
        raise PackageError(
            "包内发现真实客户/员工名称，已中止：\n  " + "\n  ".join(pii_hits[:20])
        )

    if map_hits:
        raise PackageError(
            "包内发现脱敏映射表中的真实值，已中止：\n  " + "\n  ".join(map_hits[:20])
        )
    if regex_fails:
        raise PackageError(
            "包内发现疑似真实密钥（非合成值），已中止：\n  " + "\n  ".join(regex_fails[:20])
        )

    problems = structural_check(staging)
    if problems:
        raise PackageError("结构红线未过：\n  " + "\n  ".join(problems[:20]))

    log(
        f"      文本与 {xlsx_files} 个 xlsx（{xlsx_cells} 个单元格）扫描通过"
        f"（{len(warnings)} 条合成值警告）；结构红线通过"
    )
    for w in warnings:
        log(f"      [warn] {w}")


def structural_check(staging: Path) -> list[str]:
    problems: list[str] = []
    forbidden_ext = {".db", ".sqlite", ".sqlite3", ".duckdb", ".db-journal", ".db-wal", ".db-shm"}
    for path in staging.rglob("*"):
        rel = path.relative_to(staging).as_posix()
        if path.is_dir():
            if path.name in {"__pycache__", "node_modules", "venv", ".zcode",
                             ".playwright-mcp", "脱敏数据", "backups", "docs"}:
                problems.append(f"禁止的目录：{rel}")
            continue
        if path.name in FORBIDDEN_FILE_NAMES:
            problems.append(f"禁止的本机配置文件：{rel}（只允许进本机敏感备份）")
        if path.suffix.lower() in forbidden_ext:
            problems.append(f"禁止的数据文件：{rel}")
        elif path.suffix.lower() in BINARY_EXTS:
            if not any(fnmatch.fnmatch(rel, pattern) for pattern in ALLOWED_BINARY_PATTERNS):
                problems.append(f"未登记二进制：{rel}（如确需随包，请登记到 ALLOWED_BINARY_PATTERNS）")
        if path.name.endswith(".pyc"):
            problems.append(f"禁止的字节码：{rel}")
    data_dir = staging / "backend" / "data"
    for path in data_dir.rglob("*"):
        if path.is_file() and path.name != ".gitkeep":
            problems.append(f"backend/data 只允许空目录骨架：{path.relative_to(staging).as_posix()}")
    for rel in REQUIRED_FILES:
        if not (staging / rel).exists():
            problems.append(f"缺少必需文件：{rel}")
    requirements = staging / "backend" / "requirements.txt"
    if requirements.is_file():
        text = requirements.read_text(encoding="utf-8", errors="replace").lower()
        for name in REQUIRED_REQUIREMENTS:
            if name not in text:
                problems.append(f"requirements.txt 缺少核心依赖：{name}")
    return problems


# ---------------------------------------------------------------- 打包

def make_zip(staging: Path, zip_path: Path) -> tuple[int, int, str]:
    log("[6/8] 打包 ZIP…")
    RELEASES_DIR.mkdir(parents=True, exist_ok=True)
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    top = staging.name
    if zip_path.exists():
        zip_path.unlink()
    count = 0
    digest = hashlib.sha256()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                arc = f"{top}/{path.relative_to(staging).as_posix()}"
                zf.write(path, arc)
                count += 1
    raw = zip_path.read_bytes()
    digest.update(raw)
    log(f"      {zip_path}（{count} 个文件，{len(raw)/1024/1024:.2f} MB）")
    log(f"      sha256: {digest.hexdigest()}")
    return count, len(raw), digest.hexdigest()


# ---------------------------------------------------------------- 冒烟

def free_port(start: int = 8017, limit: int = 30) -> int:
    for port in range(start, start + limit):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise PackageError(f"找不到空闲端口（{start}-{start + limit - 1}）")


def smoke(zip_path: Path, top: str) -> None:
    log("[7/8] 解压冒烟（真实起服务验证）…")
    smoke_dir = SMOKE_ROOT / top
    if smoke_dir.exists():
        shutil.rmtree(smoke_dir)
    smoke_dir.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(str(smoke_dir))
    extracted = smoke_dir / top
    backend = extracted / "backend"
    if not (backend / "app" / "main.py").exists():
        raise PackageError("解压后缺少 backend/app/main.py")

    port = free_port()
    log_file = smoke_dir / "server.log"
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    with open(log_file, "w", encoding="utf-8", errors="replace") as log_fh:
        proc = subprocess.Popen(
            [str(VENV_PY), "-m", "uvicorn", "app.main:app",
             "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(backend), stdout=log_fh, stderr=subprocess.STDOUT, env=env,
        )
    try:
        base = f"http://127.0.0.1:{port}"
        deadline = time.time() + 90
        body = b""
        while time.time() < deadline:
            if proc.poll() is not None:
                tail = log_file.read_text(encoding="utf-8", errors="replace")[-2000:]
                raise PackageError(f"服务进程提前退出（码 {proc.returncode}）：\n{tail}")
            try:
                with urllib.request.urlopen(base + "/", timeout=5) as resp:
                    if resp.status == 200:
                        body = resp.read()
                        break
            except (urllib.error.URLError, ConnectionError, OSError):
                time.sleep(1)
        else:
            tail = log_file.read_text(encoding="utf-8", errors="replace")[-2000:]
            raise PackageError(f"90 秒内服务未就绪：\n{tail}")

        if b'id="app"' not in body:
            raise PackageError("首页 200 但未包含应用挂载点 id=\"app\"（前端产物异常）")
        with urllib.request.urlopen(base + "/docs", timeout=10) as resp:
            if resp.status != 200:
                raise PackageError(f"/docs 返回 {resp.status}")
        db_file = backend / "data" / "workbench.db"
        if not db_file.exists() or db_file.stat().st_size == 0:
            raise PackageError("首次启动未在解压包内生成 workbench.db（建库/种子逻辑异常）")
        log(f"      冒烟通过：/ 200（含挂载点）、/docs 200、新库 {db_file.stat().st_size} 字节")
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        if proc.returncode not in (0, None):
            pass
    shutil.rmtree(smoke_dir, ignore_errors=True)
    log("      冒烟目录已清理；真实 backend/data 全程零接触")


# ---------------------------------------------------------------- 主流程

def main() -> int:
    parser = argparse.ArgumentParser(description="个人工作台分发包制作")
    parser.add_argument("--skip-smoke", action="store_true", help="跳过解压冒烟")
    parser.add_argument("--keep-staging", action="store_true", help="保留中间产物")
    parser.add_argument("--out", default="", help="自定义 ZIP 输出路径")
    args = parser.parse_args()

    staging: Path | None = None
    try:
        version = read_version()
        today = date.today().strftime("%Y%m%d")
        top = f"personal-workbench-v{version}-{today}"
        staging = STAGING_ROOT / top
        zip_path = Path(args.out) if args.out else RELEASES_DIR / f"个人工作台-v{version}-{today}.zip"

        log(f"个人工作台分发包制作 v{version}（{today}）")
        log(f"  产物: {zip_path}")
        preflight()
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        export_archive(staging)
        prune_internal(staging)
        build_frontend(staging, resolve_tool("npm"))
        scan_secrets(staging)
        count, size, sha = make_zip(staging, zip_path)
        if args.skip_smoke:
            log("[7/8] 已跳过解压冒烟（--skip-smoke）")
        else:
            smoke(zip_path, top)
        log("[8/8] 完成")
        log("")
        log("================ 打包结果 ================")
        log(f"  ZIP:     {zip_path}")
        log(f"  大小:    {size/1024/1024:.2f} MB（{count} 个文件）")
        log(f"  sha256:  {sha}")
        log(f"  顶层目录: {top}/")
        log("  使用: 解压 → 双击 启动工作台.bat（分发包已含前端产物，无需 Node）")
        log("==========================================")
    except PackageError as exc:
        log("")
        log(f"打包失败：{exc}")
        if staging is not None and not args.keep_staging:
            shutil.rmtree(staging, ignore_errors=True)
        return 1
    finally:
        if staging is not None and not args.keep_staging and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
