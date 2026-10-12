# expert_packs 专家团文件化 · 开发记录（2026-10-12）

> 位置：`backend/app/agents/expert_packs.py` + `backend/app/routers/experts.py` + `frontend/src/views/ExpertPackView.vue`
> 级别：阶段六-C ｜ 依赖：无新增（PyYAML 未使用，frontmatter 为专用轻量解析）

## 功能

把 Agent 团队专家定义落成 `.md` 文件（Expert Packs），与数据库 `agent_specs` 双向同步：

- **目录**：`backend/data/expert-packs`（已 gitignore）。首次创建时从 `backend/seed_experts/` 复制 8 个内置种子（幂等）；此后文件归用户所有，删除不复活，恢复出厂走「从种子重置」。
- **文件格式**（与 seed_experts 一致，即 zcode 插件 agents/<name>.md 格式）：YAML frontmatter（`name` 必填，小写字母/数字/下划线；`label/emoji/color/description/keywords/tools/sort` 可选，keywords/tools 支持逗号分隔字符串或 `[a, b]` 行内列表）+ 正文即角色系统提示词（role_prompt）。
- **导入 = upsert**：不存在则新建（`is_builtin=False`）；存在则更新 display_name/emoji/color/description/role_prompt/keywords/tools/sort，`is_builtin/enabled/model_id/skills` 保持原值；内容完全一致则不写库（action=unchanged）。
- **导出**：把 `agent_specs` 行渲染为 .md 覆盖写入包目录。
- **一致性比对**：清单接口逐文件给出 `imported`（是否已入库）与 `diff`（显示名/提示词/关键词/工具/图标/颜色/描述/排序的差异简述）。
- **缓存**：导入/导出后调用 `team.invalidate_team_cache()`，`load_team()` 的 60 秒 TTL 缓存立即失效，团队配置即时生效（冒烟测试经 route-test 实测验证）。

## 集成点

- `backend/app/main.py` lifespan：`seed_agent_specs` 之后增加 `expert_packs.packs_dir()` 调用（try/except 仅记日志），保证首次启动完成种子复制。这是唯一改动的既有文件（+1 import、+4 行）。

## API（/api/experts）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /packs | 文件清单（含 valid/error/imported/diff 一致性状态） |
| POST | /packs/import-all | 导入全部有效包，返回 {imported, skipped, errors} |
| POST | /packs/import/{name} | 导入指定包（upsert）；文件不存在 404、解析失败 400（中文 detail） |
| POST | /packs/reset | 从种子重置包目录（覆盖同名文件）——前端「从种子重置」按钮所需 |
| POST | /packs/export/{expert_name} | 把数据库专家导出为 .md（覆盖同名）；专家不存在 404 |
| GET | /packs/{name}/content | 查看 .md 原文 |
| DELETE | /packs/{name} | 删除包文件（不影响数据库） |

防穿越：名称须匹配 `^[a-z][a-z0-9_]*$`，解析后的路径父目录必须为包目录，双重校验；非法名称 400。

## 前端

`ExpertPackView.vue`（路由 /expert-packs，菜单「专家团文件」）：左右双表——左「文件专家包」（名称/描述/状态[已导入·未导入·有差异·文件错误，差异与错误走 tooltip]/操作：导入、查看、删除），右「数据库专家」（名称/显示名/是否内置/操作：导出为文件）；顶部「全部导入」「从种子重置」；查看对话框展示 .md 原文（pre）。axios 直接用 `import api from '../api'`（未改 api/index.js）。

## 测试与验收

- `backend/tests/test_expert_packs.py`：**27 例全绿**（`cd backend && ./venv/Scripts/python.exe -m pytest tests/test_expert_packs.py -q`）。覆盖：解析（好文件/默认值/行内列表/缺 name/缺正文/无 frontmatter/name 不合法）、import 新建与更新（is_builtin 保持、enabled 保持、unchanged 短路、不存在 404）、import-all（imported/skipped/errors）、export 往返（改库→导出→删库中专家→再导入→字段一致）、清单三态、读/删、防穿越（服务层 6 类非法名 + 目录外诱饵文件不被触碰）、reset、HTTP 端点全链（含缓存失效的行为验证：route-test 命中新导入专家）。
- 全量后端套件：**267 passed**（main.py lifespan 改动无回归）。
- 前端：`npm run build` 通过；`npm test`（vitest）**50 passed**。
- 真实 uvicorn 端到端冒烟（独立临时库、端口 8231）：**18/18 通过**——首次启动 8 种子落盘、7 位内置「已导入」+ 财税专员「有差异(工具)」、import-all、导出、自定义包导入后路由即时命中、删文件不动数据库、reset、穿越拦截、404 中文 detail。

## 已知限制

- **财税专员常态显示「有差异(工具不同)」**：财务模块 manifest 的 `preset_experts` 会在启动时把模块工具并入数据库行（17 个），而种子文件按设计只列内置工具（6 个，见 team.py 注释）。这是真实差异而非 bug；「导入」会让数据库与文件一致（模块工具被文件内容替换），diff 提示即为此而设。
- **完整测试套件会写真实 data 目录**：排在前面的测试模块（如 test_agent_specs.py）启动应用时触发 lifespan 种子复制，在真实 `backend/data/expert-packs` 生成 8 个种子文件（与生产首次启动行为一致、幂等、已 gitignore）。本测试文件单独运行（任务给定命令）不碰真实目录——已用会话级 fixture 重定向 + 删除目录重跑实验双重验证。
- frontmatter 为轻量解析：不支持行尾注释（`#` 开头整行注释支持）；值内含半角逗号的关键词/工具名无法往返（现有数据均不含）。
- 包目录中两个文件 frontmatter `name` 相同属用户错误，import-all 按文件排序后者覆盖前者，不报错。
