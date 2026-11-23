"""PII 门禁合成测试（Task 1 + fix round 1）。

约束：
- 敏感样例全部在运行时由片段拼接，测试源码本身可被 ``dev/check_pii.py`` 扫描而不命中；
- 占位符同样要防 smudge：占位符本身就是 clean/smudge 映射表的钥匙，完整写在源码里会被
  工作区过滤器还原成真实路径/公司名，使 ``test_placeholders_are_not_flagged`` 变红且
  ``git status`` 看不出来；所以 ``PLACEHOLDERS`` 里命中映射钥匙的 3 项也拆成片段运行时拼接
  （运行时的值仍与 HEAD 的安全占位完全一致）；
- 不读取 ``.env``、``dev/sanitize-map.local.json``、``dev/pii-terms.local.txt``
  或任何真实业务数据；xlsx/git 仓库用例只在 pytest 临时目录生成；
- 断言输出只含文件/规则/位置，绝不回显命中值。
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DEV = REPO / "dev"


def _load(filename: str, module_name: str):
    path = DEV / filename
    assert path.is_file(), f"缺少 {filename}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def pii():
    return _load("check_pii.py", "check_pii_under_test")


@pytest.fixture(scope="module")
def filt():
    return _load("git_sanitize_filter.py", "sanitize_filter_under_test")


@pytest.fixture(scope="module")
def finance():
    return _load("desensitize_finance_data.py", "desensitize_finance_under_test")


@pytest.fixture(scope="module")
def board():
    return _load("desensitize_board_data.py", "desensitize_board_under_test")


@pytest.fixture(scope="module")
def manage():
    return _load("desensitize_manage_reports.py", "desensitize_manage_under_test")


WXID = "wx" + "id_" + "a1b2c3d4e5f60718"
COMPANY = "蓝" + "海" + "星" + "辰" + "商业" + "管理" + "有限公司"
REGION_COMPANY_4 = "昆" + "明" + "甲" + "乙" + "有限公司"
REGION_COMPANY_6 = "云" + "南" + "戊" + "己" + "庚" + "辛" + "有限公司"
TOKEN = "sk-" + ("a1b2c3d4e5f6" * 4)
SK_ANT = "sk-" + "ant-" + "api03-" + "a1b2c3d4e5f6g7h8i9j0"
SK_MIXED = "sk-" + "a1b2c3d4-" + "e5f6g7h8_" + "i9j0k1l2"
USER_PATH = "C:" + "\\" + "Users" + "\\" + "sample" + "\\" + "notes.txt"
DATA_PATH = "D:" + "\\" + "数据" + "\\" + "归档"
ASCII_PATH = "D:" + "/" + "projects" + "/" + "pw" + "/" + "data"
SPACE_PATH = "D:" + "\\" + "My" + " " + "Data" + "\\" + "secret.xlsx"
PLACEHOLDERS = (
    "<本机路径>",
    "D:/path/to/project",
    # 以下 3 项是 clean/smudge 映射表的钥匙，拆片段后可避免源码出现完整字节序列
    "D:" + "\\" + "Asset" + "Lib" + "\\" + "project",
    "C:/tmp/cache/file.txt",
    "D:/tmp/work/file.txt",
    "Z:/no/engine",
    "C:/Windows/Fonts/simsun.ttc",
    "C:/Program Files/LibreOffice/program/soffice.exe",
    "D:/…",
    "wxid_***",
    "示例科技有限公司",
    "某" + "商业" + "管理" + "公司",
    "脱" + "敏" + "单位",
    "sk-<REDACTED>",
    "sk-synthetic-key",
    "C:/Users/<用户名>/.workbuddy",
)


def _init_repo(path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    return path


# ---------- 规则 ----------

def test_wechat_id_is_flagged(pii):
    findings = pii.scan_text(f"root={WXID}", path="sample.txt")
    assert any(f.rule == "wechat_id" and f.location == "1" for f in findings)


def test_wechat_directory_marker_is_flagged(pii):
    findings = pii.scan_text("dir=" + "xwechat" + "_files", path="sample.txt")
    assert any(f.rule == "wechat_dir" for f in findings)


@pytest.mark.parametrize("value", (USER_PATH, DATA_PATH, ASCII_PATH, SPACE_PATH))
def test_windows_absolute_path_is_flagged(pii, value):
    findings = pii.scan_text(f"dir={value}", path="sample.txt")
    assert any(f.rule == "win_path" for f in findings), value


@pytest.mark.parametrize("value", (COMPANY, REGION_COMPANY_4, REGION_COMPANY_6))
def test_company_full_name_is_flagged(pii, value):
    findings = pii.scan_text(f"甲方：{value}", path="sample.txt")
    assert any(f.rule == "company" for f in findings), value


@pytest.mark.parametrize("value", (TOKEN, SK_ANT, SK_MIXED))
def test_common_token_is_flagged(pii, value):
    findings = pii.scan_text(f"key={value}", path="sample.txt")
    assert any(f.rule == "token" for f in findings), value


@pytest.mark.parametrize("value", PLACEHOLDERS)
def test_placeholders_are_not_flagged(pii, value):
    assert pii.scan_text(value, path="sample.txt") == []


def test_findings_never_echo_values(pii):
    text = f"{WXID}\n{TOKEN}\n{COMPANY}\n{USER_PATH}"
    findings = pii.scan_text(text, path="sample.txt")
    rendered = "\n".join(str(f) for f in findings)
    for secret in (WXID, TOKEN, COMPANY, USER_PATH):
        assert secret not in rendered
    assert findings, "样例应至少命中一条"


def test_xlsx_cells_are_scanned(pii, tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "明细"
    sheet["A1"] = WXID
    sheet["B2"] = COMPANY
    target = tmp_path / "book.xlsx"
    book.save(target)

    findings = pii.scan_paths([target])
    locations = {(f.rule, f.location) for f in findings}
    assert ("wechat_id", "明细!A1") in locations
    assert ("company", "明细!B2") in locations


# ---------- 编码 / 词表 ----------

def test_utf16_text_is_scanned(pii, tmp_path):
    target = tmp_path / "utf16.txt"
    target.write_bytes(("id=" + WXID).encode("utf-16"))
    findings = pii.scan_paths([target])
    assert any(f.rule == "wechat_id" for f in findings)


def test_gb18030_text_is_scanned(pii, tmp_path):
    target = tmp_path / "gbk.txt"
    target.write_bytes(("甲方：" + COMPANY).encode("gb18030"))
    findings = pii.scan_paths([target])
    assert any(f.rule == "company" for f in findings)


def test_undecodable_text_raises_scan_error(pii, tmp_path):
    target = tmp_path / "binary.txt"
    target.write_bytes(b"\xff\xff\xff\xff")
    with pytest.raises(pii.ScanError):
        pii.scan_paths([target])


def test_optional_local_terms_are_loaded_without_echo(pii, tmp_path):
    term = "合成" + "客户名称"
    terms_file = tmp_path / "terms.txt"
    terms_file.write_text(term + "\n", encoding="utf-8")
    doc = tmp_path / "doc.md"
    doc.write_text(f"客户：{term}", encoding="utf-8")

    findings = pii.scan_paths([doc], terms=pii.load_terms([terms_file]))
    assert any(f.rule == "local_term" for f in findings)
    assert term not in "\n".join(str(f) for f in findings)


def test_local_term_is_not_exempted_by_placeholder_window(pii, tmp_path):
    term = "合成" + "客户名称"
    doc = tmp_path / "doc.md"
    doc.write_text("示例" + term, encoding="utf-8")
    findings = pii.scan_paths([doc], terms=[term])
    assert any(f.rule == "local_term" for f in findings)


# ---------- CLI 退出码 ----------

def test_cli_returns_nonzero_and_hides_value(pii, tmp_path, capsys):
    dirty = tmp_path / "dirty.txt"
    dirty.write_text(f"id={WXID}", encoding="utf-8")
    assert pii.main(["--path", str(dirty)]) == 1
    captured = capsys.readouterr()
    assert WXID not in captured.out + captured.err
    assert "wechat_id" in captured.out + captured.err


def test_cli_returns_zero_when_clean(pii, tmp_path):
    clean = tmp_path / "clean.txt"
    clean.write_text("hello", encoding="utf-8")
    assert pii.main(["--path", str(clean)]) == 0


def test_cli_missing_path_exits_2(pii, tmp_path):
    assert pii.main(["--path", str(tmp_path / "missing.txt")]) == 2


def test_cli_missing_terms_exits_2(pii, tmp_path):
    clean = tmp_path / "clean.txt"
    clean.write_text("hello", encoding="utf-8")
    assert pii.main(["--path", str(clean), "--terms", str(tmp_path / "missing.txt")]) == 2


def test_cli_invalid_archive_exits_2(pii):
    assert pii.main(["--archive", "no-such-revision"]) == 2


def test_corrupt_xlsx_raises_and_cli_exits_2(pii, tmp_path):
    bad = tmp_path / "bad.xlsx"
    bad.write_bytes(b"not a zip")
    with pytest.raises(pii.ScanError):
        pii.scan_paths([bad])
    assert pii.main(["--path", str(bad)]) == 2


# ---------- git 目标 ----------

def test_staged_blob_is_scanned(pii, tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "dirty.txt").write_text(f"id={WXID}", encoding="utf-8")
    subprocess.run(["git", "add", "dirty.txt"], cwd=repo, check=True)
    findings = pii.scan_staged(repo=repo)
    assert any(f.rule == "wechat_id" for f in findings)


def test_archive_is_scanned(pii, tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "dirty.txt").write_text(f"id={WXID}", encoding="utf-8")
    subprocess.run(["git", "add", "dirty.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "dirty"], cwd=repo, check=True)
    findings = pii.scan_archive("HEAD", repo=repo)
    assert any(f.rule == "wechat_id" for f in findings)


def test_clean_archive_and_staged_have_no_hits(pii, tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "clean.txt").write_text("hello", encoding="utf-8")
    subprocess.run(["git", "add", "clean.txt"], cwd=repo, check=True)
    assert pii.scan_staged(repo=repo) == []
    subprocess.run(["git", "commit", "-q", "-m", "clean"], cwd=repo, check=True)
    assert pii.scan_archive("HEAD", repo=repo) == []
    assert pii.scan_worktree(repo=repo) == []


# ---------- 过滤器 ----------

def test_sanitize_filter_masks_ids_and_tokens(filt):
    raw = f"id={WXID}\nkey={TOKEN}\nant={SK_ANT}\nmix={SK_MIXED}\n".encode()
    out = filt.sanitize_bytes(raw, mode="clean", pairs=[])
    assert WXID.encode() not in out
    assert TOKEN.encode() not in out
    assert SK_ANT.encode() not in out
    assert SK_MIXED.encode() not in out
    assert b"wxid_***" in out


def test_sanitize_filter_check_bytes(filt):
    assert filt.check_bytes(f"id={WXID}".encode()) == ["wechat_id"]
    assert "token" in filt.check_bytes(f"key={SK_ANT}".encode())
    assert filt.check_bytes(b"hello") == []


def test_sanitize_filter_keeps_patterns_and_placeholders(filt):
    samples = (
        b"wxid_[A-Za-z0-9]{6,}",
        b"sk-<REDACTED>",
        b"sk-synthetic-key",
        "C:/Users/<用户名>/.workbuddy".encode(),
    )
    for sample in samples:
        assert filt.sanitize_bytes(sample, mode="clean", pairs=[]) == sample


# ---------- 脱敏脚本 fail-closed / 独立 check ----------

def test_finance_requires_company_tokens(finance, tmp_path, monkeypatch):
    monkeypatch.setattr(finance, "LOCAL_CONFIG_FILE", tmp_path / "missing.json")
    with pytest.raises(SystemExit):
        finance.require_company_tokens()


def test_finance_accepts_local_company_tokens(finance, tmp_path, monkeypatch):
    cfg = tmp_path / "cfg.json"
    cfg.write_text('{"company_tokens": {"合成甲": "合成乙"}}', encoding="utf-8")
    monkeypatch.setattr(finance, "LOCAL_CONFIG_FILE", cfg)
    finance.require_company_tokens()
    assert finance.COMPANY_TOKENS["合成甲"] == "合成乙"


def test_manage_requires_local_mapping(manage, monkeypatch):
    monkeypatch.setattr(manage, "load_local_config", lambda: {})
    with pytest.raises(SystemExit):
        manage.require_manage_config()


def test_manage_check_does_not_self_certify(manage, tmp_path, monkeypatch):
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    book.active["A1"] = COMPANY
    target = tmp_path / "sample.xlsx"
    book.save(target)

    monkeypatch.setattr(manage, "build_mapping", lambda: {"合成": "合成"})
    monkeypatch.setattr(manage, "load_pii_terms", lambda: [])
    assert manage.check([target]) == 1

    clean_book = openpyxl.Workbook()
    clean_book.active["A1"] = "示例科技有限公司"
    clean = tmp_path / "clean.xlsx"
    clean_book.save(clean)
    assert manage.check([clean]) == 0

# ---------- fix round 2 ----------

SAMPLES = (
    WXID,
    COMPANY,
    REGION_COMPANY_4,
    REGION_COMPANY_6,
    ASCII_PATH,
    USER_PATH,
    DATA_PATH,
    TOKEN,
    SK_ANT,
    SK_MIXED,
)


def test_samples_are_not_in_synthetic_vocab(pii, tmp_path):
    vocab = tmp_path / "synthetic-terms.txt"
    vocab.write_text("合成真实客户甲\n合成真实公司乙\n", encoding="utf-8")
    terms = pii.load_terms([vocab])
    assert terms
    for sample in SAMPLES:
        assert all(sample not in term for term in terms)
        assert all(term not in sample for term in terms)


def _win(drive: str, *parts: str) -> str:
    """合成盘符路径（片段拼接，避免测试文件自身被 check_pii 命中）。"""
    return drive + ":" + "".join("/" + part for part in parts)


def test_ascii_sample_is_neutral():
    assert ASCII_PATH == _win("D", "projects", "pw", "data")
    assert "projects" in ASCII_PATH


@pytest.mark.parametrize(
    "value",
    (
        _win("D", "data", "uploads", "a.png"),
        _win("D", "saved", "a.json"),
        _win("D", "my docs", "root", "file.txt"),
        _win("D", "docs", "a.txt"),
        _win("D", "venv", "python.exe"),
        _win("C", "temp", "file.txt"),
    ),
)
def test_removed_broad_path_prefixes_are_flagged(pii, value):
    findings = pii.scan_text(f"path={value}", path="sample.txt")
    assert any(f.rule == "win_path" for f in findings), value


def test_narrow_path_safe_flags_example_like_real_path(pii):
    findings = pii.scan_text(
        "path=" + _win("D", "example-real", "data", "file.txt"), path="sample.txt"
    )
    assert any(f.rule == "win_path" for f in findings)


def test_pdf_and_zip_are_not_silently_clean(pii, tmp_path, capsys):
    for name in ("doc.pdf", "bundle.zip"):
        target = tmp_path / name
        target.write_bytes(b"not a supported container")
        assert pii.scan_paths([target]) == []
    err = capsys.readouterr().err
    assert "不支持内容扫描" in err


def test_openpyxl_missing_raises_scan_error(pii, tmp_path, monkeypatch):
    target = tmp_path / "book.xlsx"
    target.write_bytes(b"not a zip")
    monkeypatch.setitem(sys.modules, "openpyxl", None)
    with pytest.raises(pii.ScanError):
        pii.scan_paths([target])
    assert pii.main(["--path", str(target)]) == 2


FULL_MANAGE = {
    "project_company": [["合成甲", "合成乙"]],
    "brands": ["合成品牌甲"],
    "law_firms": [["合成律所甲", "合成律所乙"]],
    "staff": ["合成人员甲"],
    "defendants": ["合成被告甲"],
    "lawyers": ["合成律师甲"],
    "others": ["合成其他甲"],
}


@pytest.mark.parametrize("key", tuple(FULL_MANAGE))
def test_manage_requires_every_key(manage, monkeypatch, key):
    cfg = {k: v for k, v in FULL_MANAGE.items() if k != key}
    monkeypatch.setattr(manage, "load_local_config", lambda: {"manage_reports": cfg})
    with pytest.raises(SystemExit):
        manage.require_manage_config()


@pytest.mark.parametrize("key", tuple(FULL_MANAGE))
def test_manage_rejects_string_for_list_key(manage, monkeypatch, key):
    cfg = dict(FULL_MANAGE)
    cfg[key] = "不是列表"
    monkeypatch.setattr(manage, "load_local_config", lambda: {"manage_reports": cfg})
    with pytest.raises(SystemExit):
        manage.require_manage_config()


def test_manage_accepts_full_config(manage, monkeypatch):
    monkeypatch.setattr(manage, "load_local_config", lambda: {"manage_reports": dict(FULL_MANAGE)})
    assert manage.require_manage_config()["brands"] == ["合成品牌甲"]


def test_manage_check_warns_without_terms(manage, tmp_path, monkeypatch, capsys):
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    book.active["A1"] = "clean"
    target = tmp_path / "clean.xlsx"
    book.save(target)
    monkeypatch.setattr(manage, "load_pii_terms", lambda: [])
    assert manage.check([target]) == 0
    assert "警告" in capsys.readouterr().err


def test_finance_defaults_are_neutral(finance, board):
    assert finance.COMPANY_TOKENS
    assert all(value.startswith("合成") for value in finance.COMPANY_TOKENS.values())
    sample = "合成测试有限公司2026.xlsx"
    safe = finance._safe_filename(sample, {})
    fake = board._fake_name(sample)
    assert "合成单位" in safe and "脱敏单位" not in safe
    assert "合成单位" in fake and "脱敏单位" not in fake


def _commit_all(repo: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)


def test_push_commits_includes_intermediate_commit(pii, tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "dirty.txt").write_text(f"id={WXID}", encoding="utf-8")
    _commit_all(repo, "dirty")
    (repo / "dirty.txt").write_text("clean", encoding="utf-8")
    _commit_all(repo, "clean")
    tip = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True
    ).stdout.decode().strip()
    zero = "0" * 40
    commits = pii.push_commits(tip, zero, repo=repo)
    assert len(commits) == 2
    assert any(f.rule == "wechat_id" for f in pii.scan_archive(commits[1], repo=repo))
    assert not any(f.rule == "wechat_id" for f in pii.scan_archive(commits[0], repo=repo))
    assert pii.push_commits(zero, zero, repo=repo) == []


# ---------- Task 7D：遗留占位 / 历史本机路径清理 ----------

LEGACY_PLACEHOLDER = "示例" + "主体"  # 拼接，避免本文件被扫描规则自身命中的字面量
ASSIGNED_CLEAN_FILES = (
    "20261012New/REVERSE_07-当前运行环境概况.md",
    "20261030_0300_技能市场与MCP目录_功能实现与bugfix.md",
    "backend/app/agents/skill_market.py",
    "backend/scripts/import_qwenpaw_agents.py",
)


def _load_path(path: Path, module_name: str):
    assert path.is_file(), f"缺少 {path}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("rel", ASSIGNED_CLEAN_FILES)
def test_assigned_files_have_no_legacy_placeholder_or_local_path(pii, rel):
    """分配的 4 个文件不得再含旧占位串，也不得含被门禁判为真实本机路径的盘符路径。"""
    text = (REPO / rel).read_text(encoding="utf-8-sig")
    assert LEGACY_PLACEHOLDER not in text
    assert pii.scan_text(text, path=rel) == []


def test_import_script_requires_explicit_source(monkeypatch, capsys):
    """未给 --from / QWENPAW_WORKSPACES 时不得回退到硬编码目录，直接给出指引并退出。"""
    mod = _load_path(
        REPO / "backend" / "scripts" / "import_qwenpaw_agents.py",
        "import_qwenpaw_under_test",
    )
    monkeypatch.delenv("QWENPAW_WORKSPACES", raising=False)
    monkeypatch.setattr(sys, "argv", ["import_qwenpaw_agents.py"])
    assert mod.main() == 1
    out = capsys.readouterr().out
    assert "--from" in out
    assert "QWENPAW_WORKSPACES" in out
