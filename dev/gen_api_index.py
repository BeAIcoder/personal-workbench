"""从 FastAPI OpenAPI 生成 API.md 的全量路由索引表。

用法（必须在 backend 目录并带 PYTHONPATH，才能导入 app 包）：
    cd backend
    PYTHONPATH=. venv/Scripts/python.exe ../dev/gen_api_index.py

只读：导入 app.main 生成 schema，不启动服务、不访问数据库、不写业务数据。
纯函数 ``render_index(schema)`` 供文档契约测试复用。
"""
from __future__ import annotations

from typing import Any

METHOD_ORDER = ("get", "post", "put", "patch", "delete", "head", "options")


def render_index(schema: dict[str, Any], prefix: str = "/api") -> str:
    """把 OpenAPI schema 渲染为 Markdown 表：方法 | 路径 | 说明。"""
    paths = schema.get("paths") or {}
    rows = ["| 方法 | 路径 | 说明 |", "| --- | --- | --- |"]
    for path in sorted(paths):
        if not path.startswith(prefix):
            continue
        operations = paths[path] or {}
        for method in METHOD_ORDER:
            operation = operations.get(method)
            if not operation:
                continue
            summary = str(operation.get("summary") or "").replace("|", "\\|").strip()
            rows.append(f"| `{method.upper()}` | `{path}` | {summary} |")
    return "\n".join(rows)


def main() -> int:
    from app.main import app

    print(render_index(app.openapi()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
