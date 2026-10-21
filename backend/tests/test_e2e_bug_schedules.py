"""schedules 区域 E2E 探测发现的 bug 回归测试。

对应探测脚本 dev/e2e/probes/schedules.py：
- TC-SCH-021：PUT /api/schedules/{id} 显式传 null 时间字段应返回 422，
  而不是让 end_time <= start_time 的比较穿透成 500。
"""


def _payload(**kw):
    base = {
        "title": "月度经营分析会",
        "start_time": "2026-09-08T09:30:00",
        "end_time": "2026-09-08T11:00:00",
        "location": "会议室A",
        "all_day": False,
    }
    base.update(kw)
    return base


def test_put_null_end_time_rejected(client):
    schedule_id = client.post("/api/schedules", json=_payload()).json()["id"]
    resp = client.put(f"/api/schedules/{schedule_id}", json={"end_time": None})
    assert resp.status_code == 422
    assert "时间" in resp.text


def test_put_null_start_time_rejected(client):
    schedule_id = client.post("/api/schedules", json=_payload()).json()["id"]
    resp = client.put(f"/api/schedules/{schedule_id}", json={"start_time": None})
    assert resp.status_code == 422
    assert "时间" in resp.text


def test_put_null_time_keeps_record_intact(client):
    schedule_id = client.post("/api/schedules", json=_payload()).json()["id"]
    client.put(f"/api/schedules/{schedule_id}", json={"end_time": None})
    body = client.get(f"/api/schedules/{schedule_id}").json()
    assert body["start_time"] == "2026-09-08T09:30:00"
    assert body["end_time"] == "2026-09-08T11:00:00"
