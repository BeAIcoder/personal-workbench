# skill_store 技能包管理 · 开发记录（2026-10-12）

> 位置：`backend/app/agents/skill_store.py` + `backend/app/routers/skills.py` + `frontend/src/views/SkillPackView.vue`
> 级别：AI 助手技能池（SKILL.md）的管理层 ｜ 依赖：PyYAML（已装，无新增）

## 功能

- **store 目录**：`backend/data/skills`（由 `app.config.DATA_DIR` 推导，不存在自动创建），每个技能包一个子目录，内含 `SKILL.md`（YAML frontmatter：name/description/version + Markdown 正文）+ 任意附带文件（支持子目录）。
- **种子播种**：`ensure_seed_packs()` 把 `backend/seed_skills/*`（month-end-close、invoice-review）整目录复制进 store；store 已有同名目录则跳过，**幂等**，不覆盖用户改动。`app/main.py` lifespan 启动时调用一次（失败仅记日志不拖垮宿主）。
- **扫描校验**：`scan_packs()` 逐目录解析 frontmatter；坏 YAML / 缺 name / 缺 SKILL.md 标记 `valid=False + error`，**不抛异常**，name 回退为目录名仍可管理。
- **启停**：store 根目录 `_store.json` 维护 `{"disabled": [...]}`（存目录名）；`set_enabled()` 读写，停用状态跨重启/重扫持久化。
- **新建/删除**：`create_pack()` 用内置模板生成 SKILL.md（名称限小写字母/数字/连字符，1-50 字符；描述必填）；`delete_pack()` 删目录并清理停用记录。
- **文件读取**：`read_pack_file()` 读包内任意文件；防路径穿越——拒绝绝对路径、`..`、包外符号链接（resolve 后校验 is_relative_to）。
- **与 Agent 的衔接**：agentscope `LocalSkillLoader`（orchestrator 的 `skills_dir` 配置）直接以目录为技能池。**若 AI 助手设置里「技能目录」为空且 store 里有启用的包，在设置里把技能目录指向 `backend/data/skills` 即可**（本功能不自动改设置）；停用的包目前仍会被 LocalSkillLoader 扫到，需要 Agent 侧按 disabled 过滤（见已知限制）。

## API（/api/skills）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /packs | 技能包清单（name/version/description/dir/files/enabled/valid/error） |
| POST | /packs | 新建技能包（201；body: name/description/body，body 留空用内置模板） |
| DELETE | /packs/{name} | 删除技能包整目录（name 即目录名） |
| PUT | /packs/{name}/enabled | 启用/停用（body: {enabled: bool}） |
| GET | /packs/{name}/files/{filename} | 读包内文件内容（filename 支持子目录相对路径，:path） |
| POST | /rescan | 重新扫描并补齐种子包，返回 {items, seeded} |

所有错误为 HTTPException + 中文 detail：400 非法名称/路径，404 包或文件不存在。

## 前端

`/#/skill-packs` 技能包管理页：表格（名称/版本/描述/状态开关/文件数/操作）+ 新建对话框（名称/描述/正文）+ 文件查看对话框（下拉选文件，pre 展示 Markdown）+ 重新扫描按钮；无效包在表格下方红色警示条展示 error。接口走 `import api from '../api'`。

## 测试与验收

- `backend/tests/test_skill_store.py`：**9 例全绿**（store_dir 全部 monkeypatch 到 tmp_path，不写真实 data 目录）——seed 复制幂等且不覆盖改动、扫描/校验（坏 YAML/缺 name/无 frontmatter/缺 SKILL.md/隐藏目录与 _store.json 不误扫）、`_store.json` 损坏容错、启停持久化（重扫仍在、去重）、新建/删除（模板正文、留空骨架、重名、6 类非法名、空描述、删后清理 disabled、delete 防穿越）、文件读取与防穿越（5 类非法路径 + 深子目录 + 缺失 404）、HTTP 全链（201/400/404、启停、读文件、穿越 400、删除幂等 404、rescan 不重复播种）。
- 后端全量回归：`pytest tests -q` → **240 passed**。
- 前端：`npx vitest run` → **50 passed (14 files)**；`npx vite build` 通过。
- 浏览器端到端（uvicorn 127.0.0.1:8000 + 构建产物）：列表渲染两个种子包、查看文件对话框（下拉 + pre 内容）、新建（落盘 SKILL.md 模板正确）、重新扫描、删除（确认框 + 磁盘目录移除）、无效包错误警示条——均符合预期。

## 已知限制

- **停用不过滤 Agent 侧**：LocalSkillLoader 按目录全量加载，`_store.json` 的 disabled 只影响本管理层展示；需要 Agent 真正跳过停用包时，得在 orchestrator 或 loader 层再过滤（后续可做）。
- 前端开关沿用 McpView 的 `v-model + @change` 标准模式，端点本身已由 HTTP 测试覆盖；浏览器自动化未能点到 el-switch（可访问性树未暴露 role=switch），未在真实浏览器里点过一次开关。
- 包身份 = 目录名；frontmatter name 与目录名不一致时不禁用（Agent 看到的是 frontmatter name），同名不同目录的包不去重。
- 运行任意使用 conftest `client` 夹具的测试会触发 lifespan 播种，首次执行会把两个种子包写进真实 `backend/data/skills`（幂等，与首次启动行为一致）；本测试文件自身的夹具已规避。
- 无包内文件的新建/编辑 API（只能整包新建或删除后手工改文件），文件查看为只读。
