"""Swarm 任务 assistant-security-upload 聚焦测试：AI 助手上传/发现/模型测试安全契约。

覆盖（对应审计台账上传无界读取、慢转换阻塞、Excel 行数、插件目录执行、模型测试密钥/URL）：
- 上传经共享 read_upload_limited 限额读取（超限 400、共享模块缺失明确 503 不退回无界读取）；
- Excel 第一张工作表转 CSV 受 MAX_IMPORT_ROWS 限制（超限 400 且不遗留任何文件），
  慢转换经 asyncio.to_thread 卸载（转换期间事件循环保持响应），转换失败清半成品 CSV；
- 插件/技能发现只接受 config_store.load() 中显式保存且规范化相等的目录，
  任意目录不得触发 load_qwenpaw_plugin（会执行插件后端代码），合法外部保存目录可用；
- 自定义 config/test 不复用当前激活真实密钥（空/掩码 key 需显式新值），
  自定义 base_url 经 _safe_gateway_url 校验 http(s)/凭据，provider_id 路径保持；
- 自定义配置的非字符串入参（JSON 数字/列表/字典）不在守卫内 500：按字符串口径
  走同一套校验（key 视为显式新值 / base_url 按非法地址 400），仍不借用激活密钥；
- 自定义 base_url 校验覆盖 SDK 文本/工具/图片（含 anthropic 图片）三条测试路径：
  非法 URL 一律 400 且三个测试都不发起；合法自定义配置的三个测试都用显式新 key；
  provider_id 路径的工具/图片测试同样用供应商自身密钥。

约定：
- 共享接口（app.upload.read_upload_limited / app.import_limits.MAX_IMPORT_ROWS）由 core
  任务提供；本文件用与契约一致的测试替身和 monkeypatch 小值验证消费侧行为，不生成巨型文件；
- 全部使用合成数据与临时目录（conftest 已钉住临时 SQLite/调试目录），不碰真实数据；
- 不调用真实 LLM：连通性测试替身只记录入参，base_url 指向本机废弃端口。
"""
import asyncio
import io
import threading
import time

import pytest
from fastapi import UploadFile

from app.agents import config_store
from app.routers import assistant as assistant_router

PARSE_SLEEP = 0.4   # 合成慢转换时长
PROBE_LIMIT = 0.2   # 事件循环等待上限：低于此值说明循环未被同步转换占住

_PLUGIN_MAIN = '''
from pathlib import Path

Path(__file__).with_name("EXECUTED").write_text("1", encoding="utf-8")


class _Plugin:
    def register(self, api):
        api.register_tool("demo_tool", lambda text: text, "演示工具")


plugin = _Plugin()
'''


# ---------------- 合成数据与测试替身 ----------------

def _xlsx_bytes(rows: list[list]) -> bytes:
    """openpyxl 合成 xlsx 字节（小文件，不生成巨型数据）。"""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def _fake_read_upload_limited(file, max_bytes, chunk_size=1024 * 1024):
    """与 core 契约一致的测试替身：分块累积读取，超限抛 ValueError（路由映射 400）。"""
    chunks, total = [], 0
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"文件超过大小上限（{max_bytes} 字节）")
        chunks.append(chunk)
    return b"".join(chunks)


def _patch_reader(monkeypatch):
    """把路由模块的共享读取器替换为契约替身（共享模块落地前后行为一致）。"""
    monkeypatch.setattr(assistant_router, "read_upload_limited", _fake_read_upload_limited, raising=False)


def _patch_row_limit(monkeypatch, value):
    monkeypatch.setattr(assistant_router, "MAX_IMPORT_ROWS", value, raising=False)


@pytest.fixture()
def upload_dir(tmp_path, monkeypatch):
    """上传目录改道临时目录：测试不碰 backend/data/uploads，结束自动清理。"""
    target = tmp_path / "uploads"
    target.mkdir()
    monkeypatch.setattr(assistant_router, "UPLOAD_DIR", target)
    return target


def _upload(client, session_id, filename, content):
    return client.post(
        "/api/assistant/upload",
        data={"session_id": session_id},
        files={"file": (filename, content)},
    )


def _mk_plugin(root, name: str, marker: bool = True):
    """在 root 下造一个 QwenPaw 工具型插件；main.py 导入时写 EXECUTED 标记（观测是否真被加载）。"""
    plug = root / name
    plug.mkdir(parents=True, exist_ok=True)
    (plug / "plugin.json").write_text(
        '{"id": "%s", "name": "%s", "description": "d", "entry": {"backend": "main.py"},'
        ' "meta": {"tools": [{"name": "demo_tool", "description": "演示工具"}]}}' % (name, name),
        encoding="utf-8",
    )
    (plug / "main.py").write_text(_PLUGIN_MAIN, encoding="utf-8")
    return plug


def _mk_skill_pack(root, name: str):
    pack = root / name
    pack.mkdir(parents=True, exist_ok=True)
    (pack / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name} 包\n---\n正文", encoding="utf-8")
    return pack


def _mk_provider(client, name="激活供应商", base_url="https://active.example/v1", api_key="sk-active-real-0001"):
    body = client.post(
        "/api/assistant/providers",
        json={"name": name, "protocol": "openai", "base_url": base_url, "api_key": api_key},
    ).json()
    return body["provider"]["id"]


def _spy_test_connection(monkeypatch):
    """替换连通性测试为入参记录替身：不发起任何网络调用。"""
    seen: list[dict] = []

    async def spy(cfg):
        seen.append(cfg)
        return {"ok": True, "reply": "stub"}

    monkeypatch.setattr(config_store, "test_connection", spy)
    return seen


# ---------------- 上传：共享限额读取 ----------------

def test_upload_reads_via_shared_limited_reader_with_10mb_limit(client, upload_dir, monkeypatch):
    """上传经共享 read_upload_limited 读取，并传入 10MB 口径（替换无界 file.read）。"""
    seen: list[int] = []

    async def spy(file, max_bytes, chunk_size=1024 * 1024):
        seen.append(max_bytes)
        return await _fake_read_upload_limited(file, max_bytes, chunk_size)

    monkeypatch.setattr(assistant_router, "read_upload_limited", spy, raising=False)
    resp = _upload(client, "sess-sec-01", "notes.txt", "你好".encode("utf-8"))
    assert resp.status_code == 200, resp.text
    assert seen == [10 * 1024 * 1024]
    assert resp.json()["kind"] == "file"


def test_upload_oversize_rejected_400_without_leftover(client, upload_dir, monkeypatch):
    """超限映射 400「上限」口径，且读取失败发生在落盘之前：不遗留任何文件。"""
    monkeypatch.setattr(assistant_router, "_MAX_UPLOAD_BYTES", 16, raising=False)
    _patch_reader(monkeypatch)
    resp = _upload(client, "sess-sec-02", "big.txt", b"x" * 64)
    assert resp.status_code == 400
    assert "上限" in resp.json()["detail"]
    assert not any(p.is_file() for p in upload_dir.rglob("*")), "超限上传不得遗留文件"


def test_upload_fails_closed_503_when_shared_reader_missing(client, upload_dir, monkeypatch):
    """共享上传读取器缺失：明确 503，绝不退回无界 file.read()（修复前行为）。"""
    monkeypatch.setattr(assistant_router, "read_upload_limited", None, raising=False)
    resp = _upload(client, "sess-sec-03", "notes.txt", b"hi")
    assert resp.status_code == 503


# ---------------- Excel：行数上限 / 线程卸载 / 失败清理 ----------------

def test_upload_xlsx_row_limit_rejects_400_and_leaves_no_file(client, upload_dir, monkeypatch):
    """首表行数（含表头）超 MAX_IMPORT_ROWS：400「上限」且 xlsx 与半成品 CSV 都不遗留。"""
    _patch_reader(monkeypatch)
    _patch_row_limit(monkeypatch, 2)
    resp = _upload(client, "sess-sec-04", "many.xlsx", _xlsx_bytes([["a", "b"], [1, 2], [3, 4], [5, 6]]))
    assert resp.status_code == 400
    assert "上限" in resp.json()["detail"]
    assert not any(p.is_file() for p in upload_dir.rglob("*")), "行数超限不得遗留 xlsx/CSV 半成品"


def test_upload_xlsx_503_when_row_limit_service_missing(client, upload_dir, monkeypatch):
    """行数上限共享常量缺失：明确 503，不静默放行无上限转换。"""
    _patch_reader(monkeypatch)
    _patch_row_limit(monkeypatch, None)
    resp = _upload(client, "sess-sec-05", "ok.xlsx", _xlsx_bytes([["a"], [1]]))
    assert resp.status_code == 503
    assert not any(p.is_file() for p in upload_dir.rglob("*"))


def test_upload_xlsx_within_limit_converts_first_sheet(client, upload_dir, monkeypatch):
    """行数未超限：正常转换第一张工作表为 CSV（既有行为不回归）。"""
    _patch_reader(monkeypatch)
    _patch_row_limit(monkeypatch, 1000)
    resp = _upload(client, "sess-sec-06", "ok.xlsx", _xlsx_bytes([["科目", "金额"], ["现金", 1], ["存款", 2]]))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "csv_converted" and body["name"] == "ok.csv"
    csv_text = (upload_dir / "sess-sec-06" / "ok.csv").read_text(encoding="utf-8-sig")
    assert "科目,金额" in csv_text and "现金,1" in csv_text


def test_upload_xlsx_conversion_offloaded_to_thread(upload_dir, monkeypatch):
    """慢转换经 asyncio.to_thread 卸载：转换启动后等待循环不被阻塞（探针快速返回）。"""
    real_convert = getattr(assistant_router, "_xlsx_first_sheet_to_csv", None)
    assert real_convert is not None, "未抽取同步转换函数，慢转换无从卸载到线程"

    started = threading.Event()

    def slow_convert(src, dst, max_rows):
        started.set()
        time.sleep(PARSE_SLEEP)
        return real_convert(src, dst, max_rows)

    monkeypatch.setattr(assistant_router, "_xlsx_first_sheet_to_csv", slow_convert)
    _patch_reader(monkeypatch)
    _patch_row_limit(monkeypatch, 1000)
    upload = UploadFile(filename="slow.xlsx", file=io.BytesIO(_xlsx_bytes([["a"], [1], [2]])))

    async def scenario():
        task = asyncio.create_task(assistant_router.upload_attachment(session_id="sess-sec-07", file=upload))
        # 同步 inline 执行时事件循环被占住，该等待循环无法及时返回
        t0 = time.perf_counter()
        deadline = t0 + 5
        while not started.is_set():
            await asyncio.sleep(0.001)
            if time.perf_counter() > deadline:
                pytest.fail("慢转换未启动")
        waited = time.perf_counter() - t0
        t1 = time.perf_counter()
        await asyncio.sleep(0.05)
        probe = time.perf_counter() - t1
        result = await task
        return waited, probe, result

    waited, probe, result = asyncio.run(scenario())
    assert waited < PROBE_LIMIT, f"事件循环被同步转换阻塞：等待 {waited:.3f}s"
    assert probe < PROBE_LIMIT, f"事件循环被同步转换阻塞：探针 {probe:.3f}s"
    assert result["kind"] == "csv_converted"


def test_upload_xlsx_conversion_failure_keeps_file_and_cleans_partial_csv(client, upload_dir, monkeypatch):
    """转换中途失败：清掉半成品 CSV，保留 xlsx 本体与说明（既有降级契约不回归）。"""
    _patch_reader(monkeypatch)
    _patch_row_limit(monkeypatch, 1000)

    def partial_then_fail(src, dst, max_rows):
        dst.write_text("partial", encoding="utf-8")
        raise RuntimeError("磁盘炸了")

    monkeypatch.setattr(assistant_router, "_xlsx_first_sheet_to_csv", partial_then_fail, raising=False)
    resp = _upload(client, "sess-sec-08", "half.xlsx", _xlsx_bytes([["a"], [1]]))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "file" and body["name"] == "half.xlsx"
    assert "Excel 转 CSV 失败" in body["note"]
    session_dir = upload_dir / "sess-sec-08"
    assert (session_dir / "half.xlsx").is_file(), "上传成功的 xlsx 应保留"
    assert not (session_dir / "half.csv").exists(), "半成品 CSV 必须清理"


# ---------------- 插件发现：已保存目录边界 ----------------

def test_plugins_discover_rejects_unsaved_dir_and_never_executes(client, tmp_path, monkeypatch):
    """任意目录不得触发 load_qwenpaw_plugin：未保存目录返回明确提示且插件后端代码零执行。"""
    good = _mk_plugin(tmp_path / "plugins_good", "goodplug")
    evil = _mk_plugin(tmp_path / "plugins_evil", "evilplug")
    assert client.put("/api/assistant/config", json={"plugins_dir": str(good.parent)}).status_code == 200

    resp = client.get("/api/assistant/plugins/discover", params={"dir": str(evil.parent)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["loadable"] == 0 and body["plugins"] == []
    assert "保存" in body.get("error", "")
    assert not (evil / "EXECUTED").exists(), "未保存目录不得执行插件后端代码"


def test_plugins_discover_allows_saved_external_dir_with_normalization(client, tmp_path, monkeypatch):
    """合法外部保存目录可用；规范化相等（尾分隔符/大小写/./斜杠）不误拒。"""
    root = tmp_path / "plugins_norm"
    plug = _mk_plugin(root, "normplug")
    assert client.put("/api/assistant/config", json={"plugins_dir": str(root)}).status_code == 200

    variants = [str(root) + "/", str(root).upper(), str(root) + "/./", str(root).replace("\\", "/")]
    for variant in variants:
        resp = client.get("/api/assistant/plugins/discover", params={"dir": variant})
        assert resp.status_code == 200, variant
        body = resp.json()
        assert body["loadable"] == 1 and body["plugins"][0]["name"] == "normplug", variant
    assert (plug / "EXECUTED").is_file(), "已保存目录应真实加载插件"

    # 近似目录（兄弟/子目录）即使存在也不放行
    sibling = tmp_path / "plugins_norm_sibling"
    sibling.mkdir()
    resp = client.get("/api/assistant/plugins/discover", params={"dir": str(sibling)})
    assert resp.json()["loadable"] == 0 and "保存" in resp.json().get("error", "")
    resp = client.get("/api/assistant/plugins/discover", params={"dir": str(root / "normplug")})
    assert resp.json()["loadable"] == 0 and "保存" in resp.json().get("error", "")


def test_plugins_discover_saved_but_missing_dir_reports_not_exist(client, tmp_path):
    """已保存目录在磁盘上不存在：沿用「目录不存在」契约（200 + error）。"""
    missing = tmp_path / "no_such_plugins"
    assert client.put("/api/assistant/config", json={"plugins_dir": str(missing)}).status_code == 200
    resp = client.get("/api/assistant/plugins/discover", params={"dir": str(missing)})
    assert resp.status_code == 200
    assert resp.json()["loadable"] == 0 and "目录不存在" in resp.json()["error"]


# ---------------- 技能发现：已保存目录边界 ----------------

def test_skills_discover_rejects_unsaved_dir(client, tmp_path):
    """未保存目录不扫描：计数 0 + 明确提示（修复前会返回未保存目录里的技能）。"""
    good = tmp_path / "skills_good"
    _mk_skill_pack(good, "alpha")
    evil = tmp_path / "skills_evil"
    _mk_skill_pack(evil, "beta")
    _mk_skill_pack(evil, "gamma")
    assert client.put("/api/assistant/config", json={"skills_dir": str(good)}).status_code == 200

    resp = client.get("/api/assistant/skills/discover", params={"dir": str(evil)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 0 and body["names"] == []
    assert "保存" in body.get("error", "")


def test_skills_discover_allows_saved_dir(client, tmp_path):
    """已保存技能池目录正常扫描（既有行为不回归）。"""
    good = tmp_path / "skills_ok"
    _mk_skill_pack(good, "alpha")
    _mk_skill_pack(good, "beta")
    assert client.put("/api/assistant/config", json={"skills_dir": str(good)}).status_code == 200

    resp = client.get("/api/assistant/skills/discover", params={"dir": str(good)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2 and set(body["names"]) == {"alpha", "beta"}


def test_skills_discover_without_saved_dir_prompts_to_save(client, tmp_path):
    """从未保存过技能目录：任意入参都给「先保存」提示，而非直接扫描。"""
    pool = tmp_path / "skills_unsaved"
    _mk_skill_pack(pool, "alpha")
    resp = client.get("/api/assistant/skills/discover", params={"dir": str(pool)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 0 and "保存" in body.get("error", "")


# ---------------- 模型测试：密钥不借用 / base_url 校验 / provider_id 保持 ----------------

def test_config_test_custom_masked_key_requires_explicit_new_key(client, monkeypatch):
    """自定义配置带掩码 key（******** / ••••••后四位）：400 语义内拒绝，不借用激活真实密钥。"""
    pid = _mk_provider(client)
    mid = client.post(f"/api/assistant/providers/{pid}/models", json={"model": "m"}).json()["model"]["id"]
    assert client.post(f"/api/assistant/models/{mid}/activate").status_code == 200
    seen = _spy_test_connection(monkeypatch)

    for masked in ("********", "••••••0001"):
        resp = client.post(
            "/api/assistant/config/test",
            json={"config": {"base_url": "http://127.0.0.1:9/v1", "model": "m", "api_key": masked}},
        )
        assert resp.status_code == 200, masked
        body = resp.json()
        assert body["text"]["ok"] is False and "API Key" in body["text"]["error"], masked
    assert seen == [], "掩码 key 不得触发连通性测试（更不得借用激活真实密钥）"
    assert config_store.current_api_key() == "sk-active-real-0001"


def test_config_test_custom_empty_key_requires_explicit_new_key(client, monkeypatch):
    """自定义配置缺 key：拒绝并要求显式提供；修复前会借用激活真实密钥发起测试。"""
    pid = _mk_provider(client)
    mid = client.post(f"/api/assistant/providers/{pid}/models", json={"model": "m"}).json()["model"]["id"]
    assert client.post(f"/api/assistant/models/{mid}/activate").status_code == 200
    seen = _spy_test_connection(monkeypatch)

    resp = client.post(
        "/api/assistant/config/test",
        json={"config": {"base_url": "http://127.0.0.1:9/v1", "model": "m"}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["text"]["ok"] is False and "API Key" in body["text"]["error"]
    assert seen == [], "空 key 不得借用激活真实密钥"


def test_config_test_custom_base_url_rejected_when_not_http_or_with_credentials(client, monkeypatch):
    """自定义 base_url 非 http(s) 或内嵌凭据：400 且不发起连通性测试。"""
    _mk_provider(client)
    seen = _spy_test_connection(monkeypatch)

    for bad in ("ftp://x.example/v1", "https://user:pass@x.example/v1", "javascript:alert(1)"):
        resp = client.post(
            "/api/assistant/config/test",
            json={"config": {"base_url": bad, "model": "m", "api_key": "sk-explicit-1"}},
        )
        assert resp.status_code == 400, bad
    assert seen == [], "非法 base_url 不得发起连通性测试"


def test_config_test_provider_id_path_unchanged(client, monkeypatch):
    """provider_id 正常路径保持：用供应商自身配置（含其密钥）发起测试。"""
    pid = _mk_provider(client, base_url="https://prov.example/v1", api_key="sk-provider-0002")
    mid = client.post(f"/api/assistant/providers/{pid}/models", json={"model": "m"}).json()["model"]["id"]
    seen = _spy_test_connection(monkeypatch)

    resp = client.post("/api/assistant/config/test", json={"provider_id": pid})
    assert resp.status_code == 200, resp.text
    assert resp.json()["text"]["ok"] is True
    assert len(seen) == 1
    assert seen[0]["api_key"] == "sk-provider-0002" and seen[0]["base_url"] == "https://prov.example/v1"


def test_config_test_custom_valid_config_uses_explicit_key(client, monkeypatch):
    """自定义配置携带显式新 key + 合法 http(s) base_url：按该配置测试，不换成激活密钥。"""
    pid = _mk_provider(client)
    mid = client.post(f"/api/assistant/providers/{pid}/models", json={"model": "m"}).json()["model"]["id"]
    assert client.post(f"/api/assistant/models/{mid}/activate").status_code == 200
    seen = _spy_test_connection(monkeypatch)

    resp = client.post(
        "/api/assistant/config/test",
        json={"config": {"base_url": "https://custom.example/v1", "model": "m", "api_key": "sk-explicit-9"}},
    )
    assert resp.status_code == 200, resp.text
    assert len(seen) == 1
    assert seen[0]["api_key"] == "sk-explicit-9" and seen[0]["base_url"] == "https://custom.example/v1"


def test_config_test_custom_non_string_key_does_not_500_or_borrow(client, monkeypatch):
    """自定义配置 api_key 为非字符串真值（数字/列表/字典/布尔）：守卫不得 500（AttributeError），
    按字符串口径视为显式新值走测试，仍绝不借用当前激活真实密钥。"""
    pid = _mk_provider(client)
    mid = client.post(f"/api/assistant/providers/{pid}/models", json={"model": "m"}).json()["model"]["id"]
    assert client.post(f"/api/assistant/models/{mid}/activate").status_code == 200
    seen = _spy_test_connection(monkeypatch)

    for bad_key in (123, ["sk-x"], {"k": "v"}, True):
        resp = client.post(
            "/api/assistant/config/test",
            json={"config": {"base_url": "http://127.0.0.1:9/v1", "model": "m", "api_key": bad_key}},
        )
        assert resp.status_code == 200, bad_key
        assert resp.json()["text"]["ok"] is True, bad_key
        assert isinstance(seen[-1]["api_key"], str), bad_key
        assert seen[-1]["api_key"] != "sk-active-real-0001", bad_key


def test_config_test_custom_non_string_base_url_rejected_400_without_500(client, monkeypatch):
    """自定义配置 base_url 为非字符串真值：按非法网关地址 400（守卫不得 500），不发起连通性测试。"""
    _mk_provider(client)
    seen = _spy_test_connection(monkeypatch)

    for bad_url in (123, ["https://x.example/v1"], {"u": "https://x.example/v1"}, True):
        resp = client.post(
            "/api/assistant/config/test",
            json={"config": {"base_url": bad_url, "model": "m", "api_key": "sk-explicit-1"}},
        )
        assert resp.status_code == 400, bad_url
    assert seen == [], "非法 base_url 不得发起连通性测试"


# ---------------- custom URL 校验覆盖 SDK/图片/工具测试 ----------------

def test_config_test_custom_bad_url_rejected_with_image_and_tools(client, monkeypatch):
    """非法自定义 base_url：即使带上 include_image/test_tools 也 400，三个测试都不发起。"""
    _mk_provider(client)
    seen_text = _spy_test_connection(monkeypatch)
    seen_tools: list[dict] = []
    seen_image: list[dict] = []

    async def spy_tools(cfg):
        seen_tools.append(cfg)
        return {"ok": True}

    async def spy_image(cfg):
        seen_image.append(cfg)
        return {"ok": True, "supported": True}

    monkeypatch.setattr(config_store, "test_tool_calling", spy_tools)
    monkeypatch.setattr(config_store, "test_multimodal", spy_image)

    resp = client.post(
        "/api/assistant/config/test",
        json={
            "config": {"base_url": "ftp://x.example/v1", "model": "m", "api_key": "sk-explicit-1"},
            "include_image": True,
            "test_tools": True,
        },
    )
    assert resp.status_code == 400
    assert seen_text == [] and seen_tools == [] and seen_image == []


def test_config_test_custom_valid_config_tools_and_image_use_explicit_key(client, monkeypatch):
    """自定义合法配置：SDK 文本/工具/图片三个测试都用显式新 key 与自定义 URL，绝不借用激活真实密钥。"""
    pid = _mk_provider(client)
    mid = client.post(f"/api/assistant/providers/{pid}/models", json={"model": "m"}).json()["model"]["id"]
    assert client.post(f"/api/assistant/models/{mid}/activate").status_code == 200
    seen_text = _spy_test_connection(monkeypatch)
    seen_tools: list[dict] = []
    seen_image: list[dict] = []

    async def spy_tools(cfg):
        seen_tools.append(cfg)
        return {"ok": True}

    async def spy_image(cfg):
        seen_image.append(cfg)
        return {"ok": True, "supported": True}

    monkeypatch.setattr(config_store, "test_tool_calling", spy_tools)
    monkeypatch.setattr(config_store, "test_multimodal", spy_image)

    resp = client.post(
        "/api/assistant/config/test",
        json={
            "config": {"base_url": "https://custom.example/v1", "model": "m", "api_key": "sk-explicit-9"},
            "include_image": True,
            "test_tools": True,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["text"]["ok"] is True and body["tools"]["ok"] is True and body["image"]["ok"] is True
    for seen in (seen_text, seen_tools, seen_image):
        assert len(seen) == 1
        assert seen[0]["api_key"] == "sk-explicit-9" and seen[0]["base_url"] == "https://custom.example/v1"


def test_config_test_custom_valid_config_anthropic_image_uses_explicit_key(client, monkeypatch):
    """anthropic 协议自定义配置：图片测试走 test_multimodal_anthropic，同样用显式新 key（不借用激活密钥）。"""
    pid = _mk_provider(client)
    mid = client.post(f"/api/assistant/providers/{pid}/models", json={"model": "m"}).json()["model"]["id"]
    assert client.post(f"/api/assistant/models/{mid}/activate").status_code == 200
    _spy_test_connection(monkeypatch)
    seen_image: list[dict] = []

    async def spy_image_anthropic(cfg):
        seen_image.append(cfg)
        return {"ok": True, "supported": True}

    monkeypatch.setattr(config_store, "test_multimodal_anthropic", spy_image_anthropic)

    resp = client.post(
        "/api/assistant/config/test",
        json={
            "config": {"protocol": "anthropic", "base_url": "https://custom.example/v1", "model": "m", "api_key": "sk-explicit-9"},
            "include_image": True,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["image"]["ok"] is True
    assert len(seen_image) == 1
    assert seen_image[0]["api_key"] == "sk-explicit-9" and seen_image[0]["base_url"] == "https://custom.example/v1"


def test_config_test_provider_id_tools_and_image_use_provider_key(client, monkeypatch):
    """provider_id 正常路径保持：工具/图片测试同样用该供应商自身配置（含其密钥），不借用其他密钥。"""
    pid = _mk_provider(client, base_url="https://prov.example/v1", api_key="sk-provider-0002")
    client.post(f"/api/assistant/providers/{pid}/models", json={"model": "m"})
    seen_text = _spy_test_connection(monkeypatch)
    seen_tools: list[dict] = []
    seen_image: list[dict] = []

    async def spy_tools(cfg):
        seen_tools.append(cfg)
        return {"ok": True}

    async def spy_image(cfg):
        seen_image.append(cfg)
        return {"ok": True, "supported": True}

    monkeypatch.setattr(config_store, "test_tool_calling", spy_tools)
    monkeypatch.setattr(config_store, "test_multimodal", spy_image)

    resp = client.post(
        "/api/assistant/config/test",
        json={"provider_id": pid, "include_image": True, "test_tools": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["text"]["ok"] is True and body["tools"]["ok"] is True and body["image"]["ok"] is True
    for seen in (seen_text, seen_tools, seen_image):
        assert len(seen) == 1
        assert seen[0]["api_key"] == "sk-provider-0002" and seen[0]["base_url"] == "https://prov.example/v1"
