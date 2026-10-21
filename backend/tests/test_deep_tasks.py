"""deep2 tasks 区域回归：更新接口的标题清洗与空值防护。

探针 TASKS-D2-001 / 002 / 003 发现：
- PUT 纯空白标题穿透 ``TaskUpdate``（无 title 清洗器），committed 后由 ``TaskOut``
  响应校验抛 ``ResponseValidationError`` → 500，且脏行会让详情/列表持续 500；
- PUT 首尾空格标题返回体被 ``TaskOut`` 清洗、库里却保留原串，前后端口径不一致；
- PUT 显式 null 给非空列（title/description/status/priority/category）触发
  IntegrityError → 500，本应是可读的 422。
"""
from app.database import SessionLocal
from app.models import Task


def _payload(**kw):
    base = {
        "title": "回归-原始标题",
        "description": "原始描述",
        "priority": "high",
        "category": "税务申报",
        "due_date": "2027-03-01T10:00:00",
    }
    base.update(kw)
    return base


def _create(client, **kw):
    resp = client.post("/api/tasks", json=_payload(**kw))
    assert resp.status_code == 201
    return resp.json()


def _raw(task_id):
    """绕过响应模型，直接读库，验证真正落库的值。"""
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        assert task is not None
        return {
            "title": task.title,
            "description": task.description,
            "status": task.status,
            "priority": task.priority,
            "category": task.category,
        }


def test_update_blank_title_is_422_and_row_survives(client):
    task = _create(client)

    resp = client.put(f"/api/tasks/{task['id']}", json={"title": "   "})

    assert resp.status_code == 422, resp.text
    assert "空白" in resp.json()["detail"]
    # 关键：不能先落库再在响应校验里炸掉，否则详情/列表都被一条脏行拖成 500
    assert _raw(task["id"])["title"] == "回归-原始标题"
    detail = client.get(f"/api/tasks/{task['id']}")
    assert detail.status_code == 200
    assert detail.json()["title"] == "回归-原始标题"
    listing = client.get("/api/tasks")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1


def test_update_title_is_trimmed_before_persist(client):
    task = _create(client)

    resp = client.put(f"/api/tasks/{task['id']}", json={"title": "  边界-去空格  "})

    assert resp.status_code == 200, resp.text
    assert resp.json()["title"] == "边界-去空格"
    # 与 TaskCreate 的清洗语义一致：库里也必须是 trim 后的值
    assert _raw(task["id"])["title"] == "边界-去空格"
    found = client.get("/api/tasks", params={"q": "边界-去空格"}).json()
    assert [t["id"] for t in found["items"]] == [task["id"]]


def test_update_explicit_null_for_required_fields_is_422(client):
    task = _create(client)
    before = _raw(task["id"])
    for field in ("title", "description", "status", "priority", "category"):
        resp = client.put(f"/api/tasks/{task['id']}", json={field: None})
        assert resp.status_code == 422, f"{field}: {resp.status_code} {resp.text}"
        assert _raw(task["id"]) == before, f"{field} 为 null 时不应改动任何列"
    assert client.get(f"/api/tasks/{task['id']}").status_code == 200


def test_update_due_date_null_still_clears(client):
    task = _create(client)
    resp = client.put(f"/api/tasks/{task['id']}", json={"due_date": None})
    assert resp.status_code == 200, resp.text
    assert resp.json()["due_date"] is None
