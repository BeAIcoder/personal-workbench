"""工作台统计与全局搜索接口测试。"""


def test_dashboard_summary_shape(client):
    resp = client.get("/api/dashboard/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert {"task", "schedule", "note", "upcoming_tasks", "today_schedules", "recent_notes", "trend_7d"} <= set(body)
    assert len(body["trend_7d"]) == 7
    assert all("date" in x and "count" in x for x in body["trend_7d"])


def test_dashboard_counts(client):
    client.post("/api/tasks", json={"title": "待办A"})
    client.post("/api/tasks", json={"title": "进行中", "status": "in_progress"})
    client.post("/api/tasks", json={"title": "已完成", "status": "done"})
    client.post("/api/tasks", json={"title": "逾期件", "due_date": "2020-01-01T00:00:00"})
    body = client.get("/api/dashboard/summary").json()
    assert body["task"]["total"] == 4
    assert body["task"]["todo"] == 2
    assert body["task"]["in_progress"] == 1
    assert body["task"]["done"] == 1
    assert body["task"]["overdue"] == 1
    assert body["task"]["completion_rate"] == 25.0


def test_dashboard_today_trend_contains_done_task(client):
    client.post("/api/tasks", json={"title": "今天完成", "status": "done"})
    trend = client.get("/api/dashboard/summary").json()["trend_7d"]
    assert trend[-1]["count"] == 1  # 最后一天是今天


def test_dashboard_upcoming_tasks_sorted_by_due(client):
    client.post("/api/tasks", json={"title": "晚", "due_date": "2099-12-01T09:00:00"})
    client.post("/api/tasks", json={"title": "早", "due_date": "2099-01-01T09:00:00"})
    upcoming = client.get("/api/dashboard/summary").json()["upcoming_tasks"]
    assert [t["title"] for t in upcoming] == ["早", "晚"]


def test_dashboard_today_schedules(client):
    from datetime import date, datetime, timedelta

    today = datetime.now().date()
    client.post(
        "/api/schedules",
        json={
            "title": "今天上午的会",
            "start_time": f"{today.isoformat()}T09:00:00",
            "end_time": f"{today.isoformat()}T10:00:00",
        },
    )
    tomorrow = today + timedelta(days=1)
    client.post(
        "/api/schedules",
        json={
            "title": "明天的会",
            "start_time": f"{tomorrow.isoformat()}T09:00:00",
            "end_time": f"{tomorrow.isoformat()}T10:00:00",
        },
    )
    body = client.get("/api/dashboard/summary").json()
    assert [s["title"] for s in body["today_schedules"]] == ["今天上午的会"]
    assert body["schedule"]["today_count"] == 1


def test_search_across_modules(client):
    client.post("/api/tasks", json={"title": "增值税申报准备", "description": "核对进项发票"})
    client.post(
        "/api/schedules",
        json={"title": "增值税申报会议", "start_time": "2026-09-08T09:00:00", "end_time": "2026-09-08T10:00:00"},
    )
    client.post("/api/notes", json={"title": "申报注意事项", "content": "增值税申报截止 15 日"})
    body = client.get("/api/search", params={"q": "增值税"}).json()
    assert body["total"] == 3
    assert len(body["tasks"]) == 1
    assert len(body["schedules"]) == 1
    assert len(body["notes"]) == 1
    assert body["tasks"][0]["type"] == "task"
    assert body["notes"][0]["snippet"]


def test_search_requires_keyword(client):
    assert client.get("/api/search").status_code == 422


def test_search_empty_result(client):
    body = client.get("/api/search", params={"q": "完全不存在的内容xyz"}).json()
    assert body["total"] == 0
    assert body["tasks"] == [] and body["schedules"] == [] and body["notes"] == []
