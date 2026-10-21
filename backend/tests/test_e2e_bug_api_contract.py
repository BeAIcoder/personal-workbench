"""api-contract 探针回归：路径参数边界与健康检查方法。"""


def test_task_id_out_of_int64_range_returns_422(client):
    """SQLite int64 溢出不应穿透成 500。"""
    huge = "99999999999999999999"
    assert client.get(f"/api/tasks/{huge}").status_code == 422
    assert client.put(f"/api/tasks/{huge}", json={}).status_code == 422
    assert client.delete(f"/api/tasks/{huge}").status_code == 422


def test_task_valid_id_still_works(client):
    created = client.post("/api/tasks", json={"title": "边界回归"}).json()
    assert client.get(f"/api/tasks/{created['id']}").status_code == 200


def test_health_supports_head(client):
    resp = client.head("/api/health")
    assert resp.status_code == 200
    assert resp.content == b""
    assert client.get("/api/health").status_code == 200
