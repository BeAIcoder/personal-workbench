"""模块清单（ModuleManifest）定义：每个后端模块在 manifest.py 里声明一个 manifest 变量。

字段约定：
- name：模块目录名（唯一），财务域模块统一 finance_ 前缀，如 finance_hello；
- prefix：HTTP 路由前缀，必须以 /api/finance/ 开头，挂载时 include_router(router, prefix=prefix)；
- tables：本模块创建的表名清单，统一 mod_ 前缀（与宿主既有表隔离，便于盘点）；
- menu：前端菜单元数据 dict，含 group（分组名）/ icon（Element Plus 图标名）/ order（排序）；
- agent_tools：模块贡献给 Agent 团队的工具 spec 列表，元素至少含
  name / description / readonly，并带 func（可调用）、params、required（与内置 TOOL_SPECS 同构）；
- column_patches：轻量迁移列补丁 {表名: {列名: DDL}}，由宿主 _ensure_sqlite_columns 合并执行；
- requires：依赖的其它模块 name 列表，依赖未加载时本模块跳过；
- enabled：False 时整个模块不加载（路由/表/工具均不生效）。
"""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ModuleManifest:
    """后端模块清单。"""

    name: str = ""                                # 目录名（唯一）
    title: str = ""                               # 显示名
    version: str = "0.1.0"
    prefix: str = ""                              # 路由前缀，必须 /api/finance/ 开头
    tables: list[str] = field(default_factory=list)        # 模块表（须 mod_ 前缀）
    menu: dict[str, Any] = field(default_factory=dict)     # {group, icon, order}
    agent_tools: list[dict] = field(default_factory=list)  # Agent 工具 spec
    column_patches: dict[str, dict] = field(default_factory=dict)  # 轻量迁移列补丁
    requires: list[str] = field(default_factory=list)      # 依赖的模块 name
    enabled: bool = True

    def validate(self) -> list[str]:
        """校验 manifest，返回错误列表（空列表 = 通过）。"""
        errors: list[str] = []
        if not self.name:
            errors.append("name 不能为空")
        if not self.prefix.startswith("/api/finance/"):
            errors.append(f"prefix 必须以 /api/finance/ 开头，当前 {self.prefix!r}")
        for table in self.tables:
            if not str(table).startswith("mod_"):
                errors.append(f"表名 {table!r} 必须以 mod_ 开头")
        if not isinstance(self.menu, dict):
            errors.append("menu 必须是 dict（group/icon/order）")
        for tool in self.agent_tools:
            for key in ("name", "description", "readonly"):
                if key not in tool:
                    errors.append(f"agent_tools 元素 {tool.get('name', '?')!r} 缺少字段 {key}")
        if not isinstance(self.requires, list):
            errors.append("requires 必须是 list")
        return errors
