"""笔记管理接口测试。"""


def _payload(**kw):
    base = {"title": "报销审核要点", "content": "检查发票抬头与税号，附件是否齐全。", "tags": ["报销", "发票"]}
    base.update(kw)
    return base


def test_create_note_cleans_tags(client):
    body = client.post("/api/notes", json=_payload(tags=["报销", " 发票 ", "报销", "", "x" * 30])).json()
    assert body["tags"] == ["报销", "发票", "x" * 20]


def test_blank_title_rejected(client):
    assert client.post("/api/notes", json=_payload(title=" ")).status_code == 422


def test_search_title_and_content(client):
    client.post("/api/notes", json=_payload(title="报销审核要点", content="检查发票抬头"))
    client.post("/api/notes", json=_payload(title="结账流程", content="月末结账顺序"))
    assert client.get("/api/notes", params={"q": "发票"}).json()["total"] == 1
    assert client.get("/api/notes", params={"q": "结账"}).json()["total"] == 1
    assert client.get("/api/notes", params={"q": "不存在"}).json()["total"] == 0


def test_tag_filter(client):
    client.post("/api/notes", json=_payload(title="A", tags=["报销"]))
    client.post("/api/notes", json=_payload(title="B", tags=["结账"]))
    assert client.get("/api/notes", params={"tag": "报销"}).json()["total"] == 1
    assert client.get("/api/notes", params={"tag": "税务"}).json()["total"] == 0


def test_pinned_filter(client):
    client.post("/api/notes", json=_payload(title="普通", pinned=False))
    client.post("/api/notes", json=_payload(title="置顶", pinned=True))
    assert client.get("/api/notes", params={"pinned": "true"}).json()["items"][0]["title"] == "置顶"


def test_pinned_first_in_default_order(client):
    n1 = client.post("/api/notes", json=_payload(title="先创建")).json()["id"]
    n2 = client.post("/api/notes", json=_payload(title="置顶的", pinned=True)).json()["id"]
    items = client.get("/api/notes").json()["items"]
    assert items[0]["id"] == n2
    assert items[1]["id"] == n1


def test_tags_endpoint_dedup(client):
    client.post("/api/notes", json=_payload(title="A", tags=["税务", "报销"]))
    client.post("/api/notes", json=_payload(title="B", tags=["报销", "结账"]))
    assert client.get("/api/notes/tags").json()["tags"] == ["税务", "报销", "结账"]


def test_update_and_delete(client):
    note_id = client.post("/api/notes", json=_payload()).json()["id"]
    body = client.put(f"/api/notes/{note_id}", json={"pinned": True, "content": "更新后的内容"}).json()
    assert body["pinned"] is True
    assert body["content"] == "更新后的内容"
    assert body["title"] == "报销审核要点"
    assert client.delete(f"/api/notes/{note_id}").status_code == 204
    assert client.get(f"/api/notes/{note_id}").status_code == 404


def test_get_update_delete_missing(client):
    assert client.get("/api/notes/999").status_code == 404
    assert client.put("/api/notes/999", json={"title": "x"}).status_code == 404
    assert client.delete("/api/notes/999").status_code == 404
