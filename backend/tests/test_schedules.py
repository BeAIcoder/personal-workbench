"""日程管理接口测试。"""


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


def test_create_schedule(client):
    resp = client.post("/api/schedules", json=_payload())
    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "月度经营分析会"
    assert body["color"] == "#409EFF"


def test_end_must_be_after_start(client):
    assert client.post(
        "/api/schedules", json=_payload(start_time="2026-09-08T11:00:00", end_time="2026-09-08T09:00:00")
    ).status_code == 422
    assert client.post(
        "/api/schedules", json=_payload(start_time="2026-09-08T09:00:00", end_time="2026-09-08T09:00:00")
    ).status_code == 422


def test_blank_title_rejected(client):
    assert client.post("/api/schedules", json=_payload(title="  ")).status_code == 422


def test_range_filter_uses_overlap(client):
    client.post("/api/schedules", json=_payload(title="月初", start_time="2026-09-02T09:00:00", end_time="2026-09-02T10:00:00"))
    client.post("/api/schedules", json=_payload(title="月末", start_time="2026-09-28T09:00:00", end_time="2026-09-28T10:00:00"))
    assert client.get("/api/schedules", params={"start": "2026-09-01", "end": "2026-09-10"}).json()["total"] == 1
    assert client.get("/api/schedules", params={"start": "2026-09-20", "end": "2026-09-30"}).json()["items"][0]["title"] == "月末"
    assert client.get("/api/schedules", params={"start": "2026-10-01", "end": "2026-10-31"}).json()["total"] == 0
    assert client.get("/api/schedules").json()["total"] == 2


def test_keyword_search(client):
    client.post("/api/schedules", json=_payload(title="银行授信沟通", location="城东支行"))
    client.post("/api/schedules", json=_payload(title="部门例会"))
    assert client.get("/api/schedules", params={"q": "银行"}).json()["total"] == 1
    assert client.get("/api/schedules", params={"q": "支行"}).json()["total"] == 1


def test_update_time_conflict(client):
    schedule_id = client.post("/api/schedules", json=_payload()).json()["id"]
    resp = client.put(f"/api/schedules/{schedule_id}", json={"end_time": "2026-09-08T08:00:00"})
    assert resp.status_code == 422


def test_update_and_delete(client):
    schedule_id = client.post("/api/schedules", json=_payload()).json()["id"]
    body = client.put(f"/api/schedules/{schedule_id}", json={"location": "线上会议"}).json()
    assert body["location"] == "线上会议"
    assert client.delete(f"/api/schedules/{schedule_id}").status_code == 204
    assert client.get(f"/api/schedules/{schedule_id}").status_code == 404


def test_all_day_schedule(client):
    body = client.post(
        "/api/schedules",
        json=_payload(title="年度体检", all_day=True, start_time="2026-09-15T00:00:00", end_time="2026-09-15T23:59:59"),
    ).json()
    assert body["all_day"] is True
