"""任务管理接口测试。"""


def _payload(**kw):
    base = {
        "title": "整理本月进项发票",
        "description": "逐张核对后认证",
        "priority": "high",
        "due_date": "2026-09-10T17:00:00",
        "category": "税务申报",
    }
    base.update(kw)
    return base


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"


def test_create_task(client):
    resp = client.post("/api/tasks", json=_payload())
    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "整理本月进项发票"
    assert body["status"] == "todo"
    assert body["completed_at"] is None
    assert body["id"] > 0


def test_create_task_blank_title_rejected(client):
    assert client.post("/api/tasks", json=_payload(title="   ")).status_code == 422
    assert client.post("/api/tasks", json=_payload(title="")).status_code == 422


def test_create_task_invalid_status(client):
    assert client.post("/api/tasks", json=_payload(status="archived")).status_code == 422


def test_create_done_task_sets_completed_at(client):
    body = client.post("/api/tasks", json=_payload(status="done")).json()
    assert body["status"] == "done"
    assert body["completed_at"] is not None


def test_list_and_filter(client):
    client.post("/api/tasks", json=_payload(title="任务A"))
    client.post("/api/tasks", json=_payload(title="任务B", status="in_progress"))
    client.post("/api/tasks", json=_payload(title="发票核对", priority="urgent"))
    assert client.get("/api/tasks").json()["total"] == 3
    assert client.get("/api/tasks", params={"status": "in_progress"}).json()["total"] == 1
    assert client.get("/api/tasks", params={"priority": "urgent"}).json()["total"] == 1
    assert client.get("/api/tasks", params={"q": "发票"}).json()["total"] == 1
    assert client.get("/api/tasks", params={"q": "不存在的关键词"}).json()["total"] == 0


def test_overdue_filter(client):
    client.post("/api/tasks", json=_payload(title="已逾期", due_date="2020-01-01T00:00:00"))
    client.post("/api/tasks", json=_payload(title="未到期", due_date="2099-01-01T00:00:00"))
    client.post("/api/tasks", json=_payload(title="已完成但过期", status="done", due_date="2020-01-01T00:00:00"))
    items = client.get("/api/tasks", params={"overdue": "true"}).json()["items"]
    assert [t["title"] for t in items] == ["已逾期"]


def test_update_partial_keeps_other_fields(client):
    task_id = client.post("/api/tasks", json=_payload()).json()["id"]
    body = client.put(f"/api/tasks/{task_id}", json={"priority": "urgent"}).json()
    assert body["priority"] == "urgent"
    assert body["title"] == "整理本月进项发票"


def test_update_status_toggles_completed_at(client):
    task_id = client.post("/api/tasks", json=_payload()).json()["id"]
    body = client.put(f"/api/tasks/{task_id}", json={"status": "done"}).json()
    assert body["status"] == "done" and body["completed_at"] is not None
    body = client.put(f"/api/tasks/{task_id}", json={"status": "todo"}).json()
    assert body["completed_at"] is None


def test_get_update_delete_missing(client):
    assert client.get("/api/tasks/999").status_code == 404
    assert client.put("/api/tasks/999", json={"title": "x"}).status_code == 404
    assert client.delete("/api/tasks/999").status_code == 404


def test_delete_task(client):
    task_id = client.post("/api/tasks", json=_payload()).json()["id"]
    assert client.delete(f"/api/tasks/{task_id}").status_code == 204
    assert client.get(f"/api/tasks/{task_id}").status_code == 404


def test_pagination(client):
    for i in range(15):
        client.post("/api/tasks", json=_payload(title=f"任务{i:02d}"))
    data = client.get("/api/tasks", params={"page": 2, "page_size": 10}).json()
    assert data["total"] == 15
    assert len(data["items"]) == 5
    assert data["page"] == 2


def test_sort_by_due_asc_puts_no_date_last(client):
    client.post("/api/tasks", json=_payload(title="晚", due_date="2026-12-01T09:00:00"))
    client.post("/api/tasks", json=_payload(title="早", due_date="2026-09-02T09:00:00"))
    client.post("/api/tasks", json=_payload(title="无日期", due_date=None))
    items = client.get("/api/tasks", params={"sort": "due_asc"}).json()["items"]
    assert items[0]["title"] == "早"
    assert items[-1]["title"] == "无日期"


def test_due_range_filter(client):
    client.post("/api/tasks", json=_payload(title="九月初", due_date="2026-09-02T09:00:00"))
    client.post("/api/tasks", json=_payload(title="九月底", due_date="2026-09-28T09:00:00"))
    assert client.get("/api/tasks", params={"due_after": "2026-09-20"}).json()["total"] == 1
    assert client.get("/api/tasks", params={"due_before": "2026-09-10"}).json()["total"] == 1
    assert client.get("/api/tasks", params={"due_after": "2026-09-01", "due_before": "2026-09-30"}).json()["total"] == 2
