from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import FrontendStaticFiles


def _client(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>工作台</title>", encoding="utf-8")
    (tmp_path / "assets" / "app.js").write_text("console.log('ok')", encoding="utf-8")
    app = FastAPI()
    app.mount("/", FrontendStaticFiles(directory=str(tmp_path), html=True))
    return TestClient(app)


def test_entry_html_revalidates_cache(tmp_path):
    client = _client(tmp_path)

    for path in ("/", "/index.html"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-cache"


def test_hashed_asset_cache_policy_is_unchanged(tmp_path):
    response = _client(tmp_path).get("/assets/app.js")

    assert response.status_code == 200
    assert "cache-control" not in response.headers


def test_missing_old_chunk_remains_404(tmp_path):
    response = _client(tmp_path).get("/assets/TasksView-old.js")

    assert response.status_code == 404
