"""模块清单（ModuleManifest）定义：每个后端模块在 manifest.py 里声明一个 manifest 变量。

字段约定：
- name：模块目录名（唯一），财务域模块统一 finance_ 前缀，如 finance_hello；
- prefix：HTTP 路由前缀。财务模块以 /api/finance/ 开头；经营域（招商/营运/企划）
  与职能域（法务/行政人事）模块以 /api/business/ 开头；调试类模块以 /api/debug/
  开头（如 debug_center 的 /api/debug）。挂载时 include_router(router, prefix=prefix)；
- title / version：仅用于加载日志与排障，不参与任何业务判断；
- agent_tools：模块贡献给 Agent 团队的工具 spec 列表，元素至少含
  name / description / readonly，并带 func（可调用）、params、required（与内置
  TOOL_SPECS 同构）；params 为 {参数名: (JSON 类型, 中文说明)} 二元组，必填参数
  只写进 required 列表（校验共用 app/tool_schema.py，见该模块说明）；
- column_patches：轻量迁移列补丁 {表名: {列名: DDL}}，由宿主 _ensure_sqlite_columns 合并执行；
- requires：依赖的其它模块 name 列表，依赖未加载时本模块跳过；
- preset_experts：工具预置目标专家 name 列表——模块加载时把其 agent_tools 的名字
  合并进这些专家的白名单（幂等；专家不存在则记录待同步，seed 后自动补齐）；
- enabled：False 时整个模块不加载（路由/表/工具均不生效）。

**菜单与表清单不在 manifest 声明**（2026-11-17 删除 menu / tables 字段）：
- 菜单的唯一源是前端 `src/modules/*/manifest.js`（后端曾有一份同名声明，无任何消费者，
  改它不会生效，只会误导读者）；
- 建表靠模块 `models.py` 导入 + `Base.metadata.create_all`，表名以 `__tablename__` 为准。
删除后仍兼容旧写法一个发布周期：构造时传 menu/tables 会记入 `removed_fields` 并被忽略，
注册器加载时记 WARNING；待全部在途模块合并后移除该兼容（见 docs/DEPRECATIONS.md）。
"""
from dataclasses import dataclass, field
from typing import Any

from ..tool_schema import params_errors

# 已删除、过渡期仅兼容不生效的字段（跨模块合并后删除）
REMOVED_FIELDS: frozenset[str] = frozenset({"menu", "tables"})


@dataclass(init=False)
class ModuleManifest:
    """后端模块清单（字段说明见模块 docstring）。"""

    name: str = ""                                # 目录名（唯一）
    title: str = ""                               # 显示名（仅日志）
    version: str = "0.1.0"                        # 版本（仅日志）
    prefix: str = ""                              # 路由前缀，/api/finance/ 或 /api/debug/ 开头
    agent_tools: list[dict] = field(default_factory=list)  # Agent 工具 spec
    column_patches: dict[str, dict] = field(default_factory=dict)  # 轻量迁移列补丁
    requires: list[str] = field(default_factory=list)      # 依赖的模块 name
    preset_experts: list[str] = field(default_factory=list)  # 工具预置目标专家 name
    enabled: bool = True
    # 过渡期诊断：manifest 里仍声明了已删除字段（menu/tables）时记录字段名
    removed_fields: tuple[str, ...] = field(default=(), init=False, compare=False, repr=False)

    def __init__(
        self,
        *,
        name: str = "",
        title: str = "",
        version: str = "0.1.0",
        prefix: str = "",
        agent_tools: list[dict] | None = None,
        column_patches: dict[str, dict] | None = None,
        requires: list[str] | None = None,
        preset_experts: list[str] | None = None,
        enabled: bool = True,
        **removed: Any,
    ) -> None:
        unknown = sorted(set(removed) - REMOVED_FIELDS)
        if unknown:
            raise TypeError(
                f"ModuleManifest 不接受字段：{', '.join(unknown)}"
                f"（可用字段：name/title/version/prefix/agent_tools/column_patches/"
                f"requires/preset_experts/enabled）"
            )
        self.removed_fields = tuple(sorted(set(removed) & REMOVED_FIELDS))
        self.name = name
        self.title = title
        self.version = version
        self.prefix = prefix
        # 不做类型归一：类型错误必须被 validate() 检出，而不是在这里被 list() 静默改写成可迭代物
        self.agent_tools = [] if agent_tools is None else agent_tools
        self.column_patches = {} if column_patches is None else column_patches
        self.requires = [] if requires is None else requires
        self.preset_experts = [] if preset_experts is None else preset_experts
        self.enabled = enabled

    def validate(self) -> list[str]:
        """校验 manifest，返回错误列表（空列表 = 通过）。"""
        errors: list[str] = []
        if not self.name:
            errors.append("name 不能为空")
        if not (self.prefix.startswith("/api/finance/")
                or self.prefix.startswith("/api/business/")
                or self.prefix == "/api/debug"
                or self.prefix.startswith("/api/debug/")):
            errors.append(
                f"prefix 必须以 /api/finance/、/api/business/ 或 /api/debug/ 开头，"
                f"当前 {self.prefix!r}"
            )
        if not isinstance(self.agent_tools, list):
            errors.append(f"agent_tools 必须是 list，当前为 {type(self.agent_tools).__name__}")
        if not isinstance(self.column_patches, dict):
            errors.append(f"column_patches 必须是 dict，当前为 {type(self.column_patches).__name__}")
        if not isinstance(self.requires, list):
            errors.append(f"requires 必须是 list，当前为 {type(self.requires).__name__}")
        if not isinstance(self.preset_experts, list):
            errors.append(f"preset_experts 必须是 list，当前为 {type(self.preset_experts).__name__}")
        else:
            # 可解析契约：专家名必须是非空字符串且不重复，否则预置会被静默丢弃
            seen: set[str] = set()
            for expert in self.preset_experts:
                if not isinstance(expert, str) or not expert.strip():
                    errors.append(f"preset_experts 元素必须是非空字符串，当前 {expert!r}")
                    continue
                if expert != expert.strip():
                    errors.append(f"preset_experts 元素不能含首尾空白：{expert!r}")
                if expert in seen:
                    errors.append(f"preset_experts 存在重复专家：{expert!r}")
                seen.add(expert)
        if isinstance(self.agent_tools, list):
            errors.extend(self._agent_tools_errors())
        return errors

    def _agent_tools_errors(self) -> list[str]:
        errors: list[str] = []
        for tool in self.agent_tools:
            if not isinstance(tool, dict):
                errors.append(f"agent_tools 元素必须是 dict，当前为 {type(tool).__name__}")
                continue
            label = f"agent_tools 元素 {tool.get('name', '?')!r}"
            for key in ("name", "description", "func", "params", "required", "readonly"):
                if key not in tool:
                    errors.append(f"{label} 缺少字段 {key}")
            if "func" in tool and not callable(tool["func"]):
                errors.append(f"{label} 的 func 必须可调用")
            if "params" in tool:
                # 参数形状、JSON 类型白名单与 required ⊆ params 共用 tool_schema 校验
                errors.extend(params_errors(label, tool["params"], tool.get("required")))
        return errors
