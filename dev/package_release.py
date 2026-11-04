"""个人工作台分发包制作脚本（ZIP）。

用法：
    python dev/package_release.py                 # 完整流程：导出→构建→扫描→打包→冒烟
    python dev/package_release.py --skip-smoke    # 跳过解压冒烟
    python dev/package_release.py --keep-staging # 保留中间产物便于排查
    python dev/package_release.py --out <路径>    # 自定义 ZIP 输出路径

流程：
    1. 预检：git 工作区干净、node/npm 可用、脱敏映射表存在、venv 存在
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
SANITIZE_MAP = DEV / "sanitize-map.local.json"
BUILD_DIR = ROOT / "build"
STAGING_ROOT = BUILD_DIR / "release"
SMOKE_ROOT = BUILD_DIR / "smoke"
RELEASES_DIR = ROOT / "backups" / "releases"
VENV_PY = ROOT / "backend" / "venv" / "Scripts" / "python.exe"

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

# 二进制扩展名（不参与文本扫描）
BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".bmp",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".mp3", ".mp4", ".wav", ".zip", ".gz", ".pdf",
    ".db", ".sqlite", ".sqlite3", ".duckdb", ".xlsx", ".xls",
    ".exe", ".node", ".wasm", ".bin", ".so", ".dll", ".dylib",
}

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


def read_version() -> str:
    pkg = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    return str(pkg.get("version") or "0.0.0")


# ---------------------------------------------------------------- 预检

def resolve_tool(name: str) -> str:
    """解析 node/npm：Windows 上需经 PATHEXT 找到 npm.cmd 才能被 CreateProcess 执行。"""
    for candidate in (name, name + ".cmd", name + ".exe", name + ".bat"):
        found = shutil.which(candidate)
        if found:
            return found
    raise PackageError(f"未找到 {name}（前端构建需要；请确认 Node.js 24+ 已安装并在 PATH 中）")


def preflight() -> None:
    log("[1/8] 预检…")
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if status.returncode != 0:
        raise PackageError("git status 执行失败，请确认在 git 仓库内运行")
    if status.stdout.strip():
        raise PackageError(
            "git 工作区有未提交改动，archive 会漏掉它们。请先提交后再打包：\n"
            + status.stdout.strip()
        )
    if not SANITIZE_MAP.exists():
        raise PackageError(f"缺少脱敏映射表 {SANITIZE_MAP}（敏感串反查的钥匙），无法保证脱敏质量")
    if not VENV_PY.exists():
        raise PackageError(f"缺少仓库 venv（{VENV_PY}），冒烟验证需要它；确认无误可加 --skip-smoke")
    node = resolve_tool("node")
    npm = resolve_tool("npm")
    log(f"      git 干净 / 映射表在位 / venv 在位 / node={node} / npm={npm}")


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


def scan_secrets(staging: Path) -> list[str]:
    log("[5/8] 敏感串扫描（映射表反查 + 密钥正则 + 结构红线）…")
    warnings: list[str] = []

    mapping = json.loads(SANITIZE_MAP.read_text(encoding="utf-8"))
    entries = mapping.get("mappings") or []
    reals = [e.get("real", "") for e in entries if isinstance(e, dict) and len(e.get("real", "")) >= 2]
    log(f"      脱敏映射表 {len(reals)} 条真实值参与反查")

    compiled = [(re.compile(p), label) for p, label in SECRET_PATTERNS]
    map_hits: list[str] = []
    pii_hits: list[str] = []
    regex_fails: list[str] = []
    pii_terms = load_pii_terms()
    if pii_terms:
        log(f"      真实客户/员工名称 {len(pii_terms)} 条参与反查")
    else:
        warnings.append(
            "PII 词表为空：未做客户/员工名称反查（建议在 dev/pii-terms.local.txt 配置，"
            "或在 dev/sanitize-map.local.json 中补齐）"
        )

    for path in iter_text_files(staging):
        rel = path.relative_to(staging).as_posix()
        content = read_text_strict(path)
        if content is None:
            continue
        for real in reals:
            if real in content:
                map_hits.append(f"{rel} 含映射表真实值（长度 {len(real)}）")
        for term in pii_terms:
            if term in content:
                pii_hits.append(f"{rel} 含真实名称（{term[0]}…，共 {len(term)} 字）")
        for pattern, label in compiled:
            for m in pattern.finditer(content):
                ctx = content[max(0, m.start() - 80): m.end() + 80]
                item = f"{rel} 命中 {label}: {m.group(0)[:20]}…"
                if SYNTHETIC_MARKERS.search(ctx):
                    warnings.append(item + "（上下文为合成/示例值，仅警告）")
                else:
                    regex_fails.append(item)

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

    log(f"      文本扫描通过（{len(warnings)} 条合成值警告）；结构红线通过")
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

    version = read_version()
    today = date.today().strftime("%Y%m%d")
    top = f"personal-workbench-v{version}-{today}"
    staging = STAGING_ROOT / top
    zip_path = Path(args.out) if args.out else RELEASES_DIR / f"个人工作台-v{version}-{today}.zip"

    log(f"个人工作台分发包制作 v{version}（{today}）")
    log(f"  产物: {zip_path}")
    try:
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
        if not args.keep_staging:
            shutil.rmtree(staging, ignore_errors=True)
        return 1
    finally:
        if not args.keep_staging and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
