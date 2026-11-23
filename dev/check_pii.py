"""PII / 敏感信息扫描门禁。

三种扫描目标：
- 指定路径（文件或目录，文本 + xlsx 单元格）；
- 当前工作树的 tracked 文件（``--tracked``）；
- git 暂存区（``--staged``）或 ``git archive <rev>`` 的提交树（``--archive``）。

设计约束：
- 输出只含 **文件、规则类型、行/单元格位置**，绝不回显命中值；
- 本地词表只通过 ``--terms`` / ``PII_TERMS_FILE`` / ``PII_TERMS`` 显式加载，
  不读取 ``dev/sanitize-map.local.json``（反向钥匙）或 ``.env``；
- 规则内置占位符豁免（``示例``/``某``/``脱敏``/``<...>``/``AssetLib`` 等），
  避免把脱敏产物再次判成泄露；
- 命中退出码 1；路径不存在、词表缺失、损坏 xlsx、无法解码等扫描失败退出 2；干净退出 0。

用法：
    python dev/check_pii.py                     # 扫描 tracked 工作树
    python dev/check_pii.py --staged            # 暂存区 blob
    python dev/check_pii.py --archive HEAD      # 某个提交的完整树
    python dev/check_pii.py --path dev --path BUGfix.md
    python dev/check_pii.py --terms dev/pii-terms.local.txt
"""
from __future__ import annotations

import argparse
import codecs
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent

XLSX_SUFFIXES = {".xlsx", ".xlsm"}
BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".pdf",
    ".zip", ".gz", ".7z", ".rar", ".tar", ".db", ".sqlite", ".exe", ".dll",
    ".so", ".dylib", ".woff", ".woff2", ".ttf", ".otf", ".mp3", ".mp4",
    ".mov", ".avi", ".pyc",
}
SKIP_DIRS = {".git", "node_modules", "venv", "__pycache__", "dist", ".pytest_cache"}
MAX_FILE_BYTES = 64 * 1024 * 1024
PATH_WINDOW = 240
DOCUMENT_SUFFIXES = {".pdf", ".zip", ".7z", ".rar", ".gz", ".tar"}
ZERO_SHA = "0" * 40


class ScanError(RuntimeError):
    """扫描无法完成（读取失败、损坏 xlsx、非文本编码等），CLI 退出码 2。"""


# 行政区划前缀：真实公司全称通常以省/市/“中国”开头；前缀过短且无区划的
# 名称（多为测试夹具）不判为公司全称，避免误报。
_REGION = (
    "中国|北京|上海|天津|重庆|河北|山西|辽宁|吉林|黑龙江|江苏|浙江|安徽|福建|"
    "江西|山东|河南|湖北|湖南|广东|海南|四川|贵州|云南|陕西|甘肃|青海|台湾|"
    "内蒙古|广西|西藏|宁夏|新疆|香港|澳门|深圳|广州|杭州|南京|武汉|成都|西安|"
    "昆明|曲靖|大理|西双版纳|个旧"
)

WECHAT_ID = re.compile(r"\bwxid_[A-Za-z0-9_-]{8,}")
# 拆开拼接，避免本文件扫描自身时命中规则字面量。
WECHAT_DIR = re.compile("xwechat" + "_files", re.IGNORECASE)
DRIVE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]")
COMPANY = re.compile(
    rf"(?<![\u4e00-\u9fa5])(?:(?:{_REGION})[\u4e00-\u9fa5]{{0,18}}|[\u4e00-\u9fa5]{{8,20}})"
    r"(?:有限公司|有限责任公司|股份有限公司)"
)
TOKEN = re.compile(
    r"\b(?:"
    r"sk-(?:ant-|proj-|svcacct-)?[A-Za-z0-9_-]{20,}"
    r"|gh[pousr]_[A-Za-z0-9]{30,}"
    r"|github_pat_[A-Za-z0-9_]{40,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|AIza[0-9A-Za-z_-]{35}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"
    r")(?![A-Za-z0-9_-])"
)
PRIVATE_KEY = re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY")

COMPANY_SAFE = ("示例", "测试", "探测", "合成", "脱敏", "某", "占位", "演示")
PATH_SAFE = (
    "assetlib",
    "path/to",
    "path\\to",
    "某",
    "示例",
    "脱敏",
    "占位",
    "不存在",
    "<",
    ">",
    "${",
    "%",
    "…",
    "...",
)
# 明确合成/测试/系统路径：不作为真实本机路径上报。
SYNTHETIC_PATH_PREFIXES = (
    "c:/tmp",
    "d:/tmp",
    "c:/mock",
    "z:/no",
    "d:/path",
    "c:/path",
    "d:/example",
    "c:/example",
    "c:/windows",
    "c:/program",
    "c:/program files",
    "c:/program files (x86)",
    "d:/assetlib",
)
TOKEN_SAFE = ("synthetic", "test", "fake", "dummy", "example", "placeholder", "redacted", "xxx")
USER_DIRS = re.compile(r"[\\/](Users|Documents|Desktop|Downloads|OneDrive)(?:[\\/]|$)", re.IGNORECASE)


@dataclass(frozen=True, order=True)
class Finding:
    """一条命中：只保留位置与规则名，不保留命中的真实值。"""

    path: str
    rule: str
    location: str

    def __str__(self) -> str:
        return f"{self.path}:{self.location}\t{self.rule}"

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "rule": self.rule, "location": self.location}


def _path_is_sensitive(value: str) -> bool:
    """判断盘符路径窗口是否像真实本机路径；合成/系统/占位路径返回 False。"""
    token = re.match(r"[^\n\"'<>|*?`，。；：（）]*", value).group(0).rstrip()
    norm = re.sub(r"/+", "/", token.replace("\\", "/")).lower()
    if norm in ("c:/", "d:/"):
        return False
    if any(norm == prefix or norm.startswith(prefix + "/") for prefix in SYNTHETIC_PATH_PREFIXES):
        return False
    if any(marker in norm for marker in PATH_SAFE):
        return False
    match = USER_DIRS.search(norm)
    if match:
        tail = norm[match.end():].strip("/")
        if not tail:
            return False
        return not any(marker in tail for marker in PATH_SAFE)
    return True


def _company_allowed(value: str) -> bool:
    return not any(marker in value for marker in COMPANY_SAFE)


def _token_allowed(value: str) -> bool:
    return not any(marker in value.lower() for marker in TOKEN_SAFE)


def scan_text(text: str, path: str = "<text>", terms: Sequence[str] = ()) -> list[Finding]:
    """扫描一段文本；同一行同一规则只报一次，且不回显命中值。"""
    findings: list[Finding] = []
    seen: set[tuple[str, str]] = set()
    lowered_terms = [(term, term.lower()) for term in terms if len(term) >= 2]

    for line_no, line in enumerate(text.splitlines(), start=1):
        location = str(line_no)
        for rule, matcher, allowed in (
            ("wechat_id", WECHAT_ID, lambda _v: True),
            ("wechat_dir", WECHAT_DIR, lambda _v: True),
            ("company", COMPANY, _company_allowed),
            ("token", TOKEN, _token_allowed),
            ("private_key", PRIVATE_KEY, lambda _v: True),
        ):
            if (rule, location) in seen:
                continue
            for match in matcher.finditer(line):
                if allowed(match.group(0)):
                    seen.add((rule, location))
                    findings.append(Finding(path, rule, location))
                    break

        if ("win_path", location) not in seen:
            for match in DRIVE.finditer(line):
                if _path_is_sensitive(line[match.start(): match.start() + PATH_WINDOW]):
                    seen.add(("win_path", location))
                    findings.append(Finding(path, "win_path", location))
                    break

        lowered = line.lower()
        for _term, term_lower in lowered_terms:
            if term_lower in lowered:
                if ("local_term", location) not in seen:
                    seen.add(("local_term", location))
                    findings.append(Finding(path, "local_term", location))
                break

    return findings


def _scan_xlsx_bytes(data: bytes, path: str, terms: Sequence[str]) -> list[Finding]:
    try:
        import openpyxl

        workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 缺少依赖/损坏 xlsx 必须报错，不能 fail-open
        raise ScanError(f"无法解析 xlsx（缺少依赖或文件损坏）：{path}") from exc
    findings: list[Finding] = []
    seen: set[tuple[str, str]] = set()
    try:
        for sheet in workbook.worksheets:
            for row in sheet.iter_rows():
                for cell in row:
                    value = cell.value
                    if not isinstance(value, str) or not value:
                        continue
                    for hit in scan_text(value, path, terms):
                        key = (hit.rule, f"{sheet.title}!{cell.coordinate}")
                        if key in seen:
                            continue
                        seen.add(key)
                        findings.append(Finding(path, hit.rule, key[1]))
    except Exception as exc:  # noqa: BLE001
        raise ScanError(f"无法读取 xlsx 单元格：{path}") from exc
    finally:
        workbook.close()
    return findings


def scan_bytes(data: bytes, path: str, terms: Sequence[str] = ()) -> list[Finding]:
    """扫描文件字节：xlsx 走单元格；文本支持 UTF-8 / GB18030 / UTF-16。"""
    suffix = Path(path.rsplit(":", 1)[-1]).suffix.lower()
    if suffix in XLSX_SUFFIXES:
        return _scan_xlsx_bytes(data, path, terms)
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        try:
            text = data.decode("utf-16")
        except UnicodeDecodeError as exc:
            raise ScanError(f"UTF-16 解码失败：{path}") from exc
        return scan_text(text, path, terms)
    if suffix in BINARY_SUFFIXES:
        if suffix in DOCUMENT_SUFFIXES:
            print(f"提示：{suffix} 不支持内容扫描，已跳过（仅按扩展名排除）：{path}", file=sys.stderr)
        return []
    if b"\x00" in data[:4096]:
        raise ScanError(f"二进制内容无法扫描：{path}")
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ScanError(f"无法按 UTF-8/GB18030/UTF-16 解码：{path}")
    return scan_text(text, path, terms)


def scan_file(path: Path, display: str | None = None, terms: Sequence[str] = ()) -> list[Finding]:
    label = display or str(path)
    try:
        size = path.stat().st_size
        if size > MAX_FILE_BYTES and path.suffix.lower() not in XLSX_SUFFIXES:
            raise ScanError(f"文件超过 {MAX_FILE_BYTES // 1024 // 1024}MB，无法扫描：{label}")
        data = path.read_bytes()
    except ScanError:
        raise
    except OSError as exc:
        raise ScanError(f"无法读取：{label}") from exc
    return scan_bytes(data, label, terms)


def _iter_files(root: Path) -> Iterable[Path]:
    try:
        children = sorted(root.rglob("*"))
    except OSError as exc:
        raise ScanError(f"无法遍历目录：{root}") from exc
    for child in children:
        if not child.is_file():
            continue
        if any(part in SKIP_DIRS for part in child.parts):
            continue
        yield child


def scan_paths(paths: Iterable[Path], terms: Sequence[str] = ()) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        if path.is_dir():
            for child in _iter_files(path):
                findings.extend(scan_file(child, terms=terms))
        elif path.is_file():
            findings.extend(scan_file(path, terms=terms))
        else:
            raise ScanError(f"路径不存在：{path}")
    return sorted(set(findings))


def _git(args: Sequence[str], repo: Path) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True
    ).stdout


def push_commits(local_sha: str, remote_sha: str, repo: Path = REPO_ROOT) -> list[str]:
    """pre-push 范围：遍历所有待推送提交；新分支用 --not --remotes。"""
    if not local_sha or set(local_sha) == {"0"}:
        return []
    if not remote_sha or set(remote_sha) == {"0"}:
        args = ["rev-list", local_sha, "--not", "--remotes"]
    else:
        args = ["rev-list", f"{remote_sha}..{local_sha}"]
    try:
        raw = _git(args, repo)
    except subprocess.CalledProcessError as exc:
        raise ScanError(f"git rev-list 失败：{local_sha[:12]}") from exc
    return [line.decode("utf-8").strip() for line in raw.splitlines() if line.strip()]


def scan_worktree(terms: Sequence[str] = (), repo: Path = REPO_ROOT) -> list[Finding]:
    """扫描 git 记录的 tracked 文件（工作区内容）。"""
    findings: list[Finding] = []
    try:
        raw = _git(["ls-files", "-z"], repo)
    except subprocess.CalledProcessError as exc:
        raise ScanError(f"git ls-files 失败：{repo}") from exc
    for item in raw.split(b"\0"):
        if not item:
            continue
        rel = item.decode("utf-8")
        path = repo / rel
        if not path.is_file():
            continue
        findings.extend(scan_file(path, display=rel, terms=terms))
    return sorted(set(findings))


def scan_staged(terms: Sequence[str] = (), repo: Path = REPO_ROOT) -> list[Finding]:
    """扫描暂存区 blob（即将提交的实际内容）。"""
    findings: list[Finding] = []
    try:
        raw = _git(["diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR"], repo)
    except subprocess.CalledProcessError as exc:
        raise ScanError(f"git diff --cached 失败：{repo}") from exc
    for item in raw.split(b"\0"):
        if not item:
            continue
        rel = item.decode("utf-8")
        proc = subprocess.run(
            ["git", "show", f":{rel}"], cwd=repo, capture_output=True
        )
        if proc.returncode != 0:
            raise ScanError(f"无法读取暂存区 blob：{rel}")
        findings.extend(scan_bytes(proc.stdout, rel, terms))
    return sorted(set(findings))


def scan_archive(rev: str = "HEAD", terms: Sequence[str] = (), repo: Path = REPO_ROOT) -> list[Finding]:
    """扫描 ``git archive <rev>`` 的完整树（提交内容）。

    ``git archive`` 在本机会对每个文件跑 clean 过滤器（每个文件启动一次 Python），
    这里用 ``filter.*=cat`` 临时中和：归档应读取提交 blob，而不是再次脱敏。
    """
    with tempfile.TemporaryFile() as stderr_file:
        proc = subprocess.Popen(
            [
                "git",
                "-c",
                "filter.sanitize.clean=cat",
                "-c",
                "filter.sanitize.smudge=cat",
                "archive",
                "--format=tar",
                rev,
            ],
            cwd=repo,
            stdout=subprocess.PIPE,
            stderr=stderr_file,
        )
        assert proc.stdout is not None
        findings: list[Finding] = []
        failed = False
        try:
            with tarfile.open(fileobj=proc.stdout, mode="r|") as archive:
                for member in archive:
                    if not member.isfile():
                        continue
                    handle = archive.extractfile(member)
                    if handle is None:
                        continue
                    findings.extend(scan_bytes(handle.read(), f"{rev}:{member.name}", terms))
        except BaseException:
            failed = True
            proc.kill()
            raise
        finally:
            proc.stdout.close()
            try:
                code = proc.wait(timeout=120)
            except subprocess.TimeoutExpired:
                proc.kill()
                code = proc.wait()
            if code != 0 and not failed:
                stderr_file.seek(0)
                message = stderr_file.read().decode("utf-8", errors="replace").strip()
                raise ScanError(f"git archive {rev} 失败：{message}")
    return sorted(set(findings))


def load_terms(files: Iterable[Path] = ()) -> list[str]:
    """从本地词表文件与环境变量加载词条；文件缺失视为扫描失败。"""
    terms: list[str] = []
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except OSError as exc:
            raise ScanError(f"无法读取词表：{path}") from exc
        terms.extend(line.strip() for line in lines if line.strip() and not line.startswith("#"))
    env_file = os.environ.get("PII_TERMS_FILE")
    if env_file:
        path = Path(env_file)
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except OSError as exc:
            raise ScanError(f"无法读取 PII_TERMS_FILE：{path}") from exc
        terms.extend(line.strip() for line in lines if line.strip() and not line.startswith("#"))
    terms.extend(part.strip() for part in os.environ.get("PII_TERMS", "").split(","))

    seen: set[str] = set()
    result: list[str] = []
    for term in terms:
        if len(term) >= 2 and term not in seen:
            seen.add(term)
            result.append(term)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PII / 敏感信息扫描门禁")
    parser.add_argument("--path", action="append", dest="paths", default=[], help="文件或目录，可重复")
    parser.add_argument("--tracked", action="store_true", help="扫描 git tracked 工作树")
    parser.add_argument("--staged", action="store_true", help="扫描暂存区")
    parser.add_argument("--archive", nargs="?", const="HEAD", default=None, help="扫描 git archive 树，默认 HEAD")
    parser.add_argument("--terms", action="append", dest="term_files", default=[], help="本地词表文件，可重复")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出（同样不含命中值）")
    parser.add_argument("--repo", default=str(REPO_ROOT), help="git 仓库根目录（默认当前仓库）")
    args = parser.parse_args(argv)
    repo = Path(args.repo)

    try:
        terms = load_terms([Path(p) for p in args.term_files])
        findings: list[Finding] = []
        scanned_labels: set[str] = set()
        if args.paths:
            paths = [Path(p) for p in args.paths]
            findings.extend(scan_paths(paths, terms))
            scanned_labels.update(str(p) for p in paths)
        if args.staged:
            findings.extend(scan_staged(terms, repo=repo))
            scanned_labels.add("staged")
        if args.archive:
            findings.extend(scan_archive(args.archive, terms, repo=repo))
            scanned_labels.add(f"archive:{args.archive}")
        if args.tracked or not (args.paths or args.staged or args.archive):
            findings.extend(scan_worktree(terms, repo=repo))
            scanned_labels.add("tracked")
    except ScanError as exc:
        print(f"PII 扫描失败：{exc}", file=sys.stderr)
        return 2
    except (OSError, RuntimeError, subprocess.CalledProcessError, tarfile.TarError) as exc:
        print(f"PII 扫描失败：{exc}", file=sys.stderr)
        return 2

    findings = sorted(set(findings))
    if args.json:
        print(json.dumps([f.as_dict() for f in findings], ensure_ascii=False, indent=2))
    else:
        for finding in findings:
            print(finding)
    targets = "、".join(sorted(scanned_labels)) or "未指定"
    print(f"PII 扫描完成：{targets}；命中 {len(findings)} 处", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
