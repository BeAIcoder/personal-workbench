# -*- coding: utf-8 -*-
"""越界主键回归（BUG-ID-RANGE-001）。

SQLite 主键是 int64：路径里传 2**63 及以上的值会在驱动层抛
OverflowError（「Python int too large to convert to SQLite INTEGER」），
穿透成 500 + 堆栈外泄。修复分两层：

1. 核心 CRUD 路由在入口用 utils.DbId（ge=1, le=2**63-1）拦下 → 422；
2. 模块路由（财务/调试等由 manifest 挂载、数量多）由 main.py 的
   全局 OverflowError 处理器兜底 → 422。

本文件实测两层：真实接口逐个打越界 ID 必须 422，处理器本身与挂载关系单测覆盖。
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import _overflow_error_as_422, app

BIG = 9_223_372_036_854_775_808  # 2**63，SQLite INTEGER 上限之外一个

BODY = {
    "title": "越界探测", "content": "", "tags": [], "pinned": False,
    "start_time": "2026-10-20T10:00:00", "end_time": "2026-10-20T11:00:00",
    "name": "越界探测", "prompt": "探针用提示词", "command": "npx", "args": [], "env": {},
    "enabled": True, "display_name": "越界探测", "base_url": "http://127.0.0.1:9/v1",
    "api_key": "sk-test", "protocol": "openai", "model": "gpt-test",
}

# (方法, 路径模板)：全部是带整数主键的真实接口
CORE_ID_ROUTES = [
    ("GET", "/api/tasks/{id}"),
    ("PUT", "/api/tasks/{id}"),
    ("DELETE", "/api/tasks/{id}"),
    ("GET", "/api/notes/{id}"),
    ("PUT", "/api/notes/{id}"),
    ("DELETE", "/api/notes/{id}"),
    ("GET", "/api/schedules/{id}"),
    ("PUT", "/api/schedules/{id}"),
    ("DELETE", "/api/schedules/{id}"),
    ("PUT", "/api/jobs/{id}"),
    ("DELETE", "/api/jobs/{id}"),
    ("POST", "/api/jobs/{id}/run"),
    ("PUT", "/api/mcp/servers/{id}"),
    ("DELETE", "/api/mcp/servers/{id}"),
    ("POST", "/api/mcp/servers/{id}/connect"),
    ("POST", "/api/mcp/servers/{id}/disconnect"),
    ("DELETE", "/api/assistant/providers/{id}"),
    ("POST", "/api/assistant/providers/{id}/models"),
    ("POST", "/api/assistant/models/{id}/activate"),
]


@pytest.mark.parametrize("method, template", CORE_ID_ROUTES)
def test_out_of_range_id_returns_422_not_500(client, method, template):
    """入口守卫必须把越界 ID 变成 422（而不是驱动层 OverflowError 的 500）。"""
    url = template.replace("{id}", str(BIG))
    res = client.request(method, url, json=BODY if method in ("PUT", "POST") else None)
    assert res.status_code == 422, f"{method} {template} → {res.status_code}（期望 422）：{res.text[:120]}"


def test_boundary_id_inside_int64_is_not_rejected_by_the_guard(client):
    """守卫只拦越界：int64 上限内的未知 ID 仍按「不存在」处理（404），不能被误判成 422。"""
    res = client.get(f"/api/tasks/{BIG - 1}")
    assert res.status_code == 404, res.text[:120]
    res = client.get(f"/api/notes/{BIG - 1}")
    assert res.status_code == 404, res.text[:120]


def test_zero_and_negative_ids_are_rejected(client):
    """ge=1：0 与负数同样 422，不给「主键从 1 开始」的库留 500 口径。"""
    for bad in ("0", "-1"):
        assert client.get(f"/api/tasks/{bad}").status_code == 422
        assert client.get(f"/api/notes/{bad}").status_code == 422


def test_global_overflow_handler_is_mounted_on_the_app():
    """模块路由（manifest 挂载）没有逐个加守卫，兜底处理器必须挂在真实 app 上。"""
    assert OverflowError in app.exception_handlers, "main.py 的 OverflowError 兜底处理器未注册"


def test_overflow_handler_maps_to_422_json():
    """处理器本身：任何 OverflowError 都转成 422 结构化错误，不再穿透 500。"""
    mini = FastAPI()

    @mini.get("/probe/{item_id}")
    def probe(item_id: int):  # 模拟模块路由：直接进 db.get(...) 前就溢出
        raise OverflowError("Python int too large to convert to SQLite INTEGER")

    mini.add_exception_handler(OverflowError, _overflow_error_as_422)
    res = TestClient(mini).get(f"/probe/{BIG}")
    assert res.status_code == 422, res.text[:200]
    assert res.json()["detail"] == "ID 超出可接受范围"


def test_utils_dbid_bounds_match_sqlite_int64():
    """规约口径：DbId 的上界就是 SQLite INTEGER 上限，改库/换驱动时这里先报警。"""
    from app.utils import DbId

    # FastAPI 把 Path(ge=..., le=...) 的约束存进 params.metadata（Ge/Le 断言对象）
    bounds = {
        type(item).__name__: getattr(item, item.__class__.__name__.lower(), None)
        for item in DbId.__metadata__[0].metadata
    }
    assert set(bounds) == {"Ge", "Le"}, bounds
    assert bounds["Ge"] == 1
    assert bounds["Le"] == 2**63 - 1
