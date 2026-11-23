"""一次性迁移 / 幂等初始化脚本（启动路径已不再执行一次性写操作）。

用法（在 backend 目录下，任选一种）：
    venv/Scripts/python scripts/migrate.py              # 等价 all：schema + seeds + secrets
    venv/Scripts/python scripts/migrate.py schema       # 建表 + 补列 + 补索引（幂等）
    venv/Scripts/python scripts/migrate.py seeds        # 启动种子（专家/技能包/调试中心/模块预置）
    venv/Scripts/python scripts/migrate.py secrets      # 一次性秘密迁移（DPAPI 密封 / 旧明文清除）
    venv/Scripts/python scripts/migrate.py status        # 只读：统计仍未迁移的明文秘密

为什么单独成脚本（2026-11-17「启动/迁移拆分」）：
- 启动（lifespan）只做幂等建表/种子，不再改写密钥列——备份/只读演练不会意外改库；
- 一次性秘密迁移（供应商 Key、MCP env、旧版 agent_settings.api_key）只在老库首次升级
  时有意义，由运维显式执行；新写入一律密封，未迁移的明文仍可兼容读取，不会丢功能。

退出码：0 = 成功；1 = 失败（缺 DPAPI、数据库不可用等），失败原因以中文打印到 stderr。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_MODULES_LOADED = False


def _load_modules() -> None:
    """注册模块表 / 列补丁 / 工具预置（与 main.py 导入期做的事一致；进程内只做一次）。"""
    global _MODULES_LOADED
    if _MODULES_LOADED:
        return
    from fastapi import FastAPI

    from app.modules import load_modules

    load_modules(FastAPI())
    _MODULES_LOADED = True


def _schema() -> None:
    from app.bootstrap import create_schema

    _load_modules()
    create_schema()
    print("schema 迁移完成：建表 + 补列 + 补索引（幂等）。")


def _seeds() -> None:
    from app.bootstrap import run_startup_seeds

    _load_modules()
    run_startup_seeds()
    print("启动种子执行完成：知识库索引 / 专家与技能包 / 调试中心 / 模块预置（幂等）。")


def _secrets() -> None:
    from app.bootstrap import run_legacy_migrations

    results = run_legacy_migrations()
    print(
        "一次性秘密迁移完成："
        f"供应商 Key 密封 {results['provider_keys']} 条、"
        f"MCP env 密封 {results['mcp_env']} 个服务器、"
        f"旧版 agent_settings Key 清除 {results['agent_settings_keys']} 行。"
    )
    if not any(results.values()):
        print("（无待迁移项，本次未改动任何密钥列）")


def _status() -> None:
    from app.bootstrap import pending_secrets

    counts = pending_secrets()
    detail = "、".join(f"{name} {value} 条" for name, value in counts.items())
    print(f"未迁移明文秘密统计：{detail}")
    if any(counts.values()):
        print("如需处理（先停服）：venv/Scripts/python scripts/migrate.py secrets")
    else:
        print("（无需迁移）")


_STEPS = {
    "schema": _schema,
    "seeds": _seeds,
    "secrets": _secrets,
    "status": _status,
}
_ALL_ORDER = ("schema", "seeds", "secrets")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="个人工作台一次性迁移 / 幂等初始化")
    parser.add_argument(
        "action",
        nargs="?",
        default="all",
        choices=("all", *_STEPS),
        help="all=全部依次执行（默认）；status=只读统计",
    )
    args = parser.parse_args(argv)
    order = _ALL_ORDER if args.action == "all" else (args.action,)
    current = args.action
    try:
        for name in order:
            current = name
            _STEPS[name]()
    except Exception as exc:  # noqa: BLE001 - CLI 收口：中文报错 + 非 0 退出
        print(f"迁移失败（{current}）：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
