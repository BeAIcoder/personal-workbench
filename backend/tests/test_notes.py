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


def test_tag_filter_escapes_like_wildcards(client):
    """标签含 % / _ 时按字面匹配，不错位命中其他标签。"""
    client.post("/api/notes", json=_payload(title="百分号", tags=["100%完成"]))
    client.post("/api/notes", json=_payload(title="下划线", tags=["a_b"]))
    client.post("/api/notes", json=_payload(title="干扰项1", tags=["100x完成"]))
    client.post("/api/notes", json=_payload(title="干扰项2", tags=["axb"]))
    assert client.get("/api/notes", params={"tag": "100%完成"}).json()["total"] == 1
    assert client.get("/api/notes", params={"tag": "a_b"}).json()["total"] == 1
    assert client.get("/api/notes", params={"tag": "100%完成"}).json()["items"][0]["title"] == "百分号"
    assert client.get("/api/notes", params={"tag": "a_b"}).json()["items"][0]["title"] == "下划线"


def test_tag_filter_escapes_json_quotes_and_backslash(client):
    """标签含 JSON 转义字符（" 与 \\）时仍能按字面过滤。"""
    client.post("/api/notes", json=_payload(title="引号标签", tags=['a"b']))
    client.post("/api/notes", json=_payload(title="反斜杠标签", tags=["a\\b"]))
    assert client.get("/api/notes", params={"tag": 'a"b'}).json()["items"][0]["title"] == "引号标签"
    assert client.get("/api/notes", params={"tag": "a\\b"}).json()["items"][0]["title"] == "反斜杠标签"


def test_update_rejects_null_non_nullable_fields(client):
    """显式 null 不能被写入非空列：tags/pinned 的 null 会污染列表接口。"""
    note_id = client.post("/api/notes", json=_payload()).json()["id"]
    for field in ("title", "content", "tags", "pinned"):
        assert client.put(f"/api/notes/{note_id}", json={field: None}).status_code == 422, field
    body = client.get(f"/api/notes/{note_id}").json()
    assert body["title"] == "报销审核要点" and body["tags"] == ["报销", "发票"]
    assert client.get("/api/notes").json()["total"] == 1


def test_update_blank_title_rejected(client):
    """更新纯空格标题与创建同口径拒绝，不能落库后触发响应校验 500。"""
    note_id = client.post("/api/notes", json=_payload()).json()["id"]
    assert client.put(f"/api/notes/{note_id}", json={"title": "   "}).status_code == 422
    assert client.get(f"/api/notes/{note_id}").json()["title"] == "报销审核要点"



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
