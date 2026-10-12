# finance_tax 企业所得税 · 开发记录（2026-10-12）

> 位置：`backend/app/modules/finance_tax/` + `frontend/src/modules/finance-tax/`
> 级别：整体迁入（原「企业所得税计算」项目）+ 前端 Vue 重写（用户决策①）

## 移植口径（用户要求：保留原有项目）

- 原项目 `01-财税自动化/企业所得税计算/企业所得税计算/` 原位不动、独立可运行（其 974 个测试继续在原项目跑）
- 迁入内容：`models.py`（5 表）、`services/`（10 个计算/表单/导入服务）、`config/`（tax_rates/tax_rules 政策参数）、`routers/`（5 个子路由）
- 机械改写仅三类：① import 相对化（`from database/models/services/config` → 相对引用，routers 的 database 需四层点到 `app.database`）；② 路由去 `/api` 前缀（由 manifest.prefix `/api/finance/tax` 提供）；③ 表名加 `mod_tax_` 前缀 + 外键同步
- 业务逻辑（分级税率、小型微利优惠、研发加计扣除、纳税调整限额、政策规则、表单生成）**零改动**
- 踩坑记录：函数体内的延迟 import（`from services.xxx import`、`from config.tax_rules import`）sed 漏掉，靠测试 500 报错定位补改——此类移植需 grep 全量 import 而非只看行首

## API（/api/finance/tax）

| 子路由 | 端点 |
|--------|------|
| /enterprises | CRUD（列表在 `/`，注意尾斜杠） |
| /financial-import | `POST /import`、`GET /template`、`/template/balance-sheet`、`/template/income-statement` |
| /policies | 列表/启停/克隆（原项目 seed 6 条已迁移） |
| /tax-returns | CRUD、`PUT /{id}/submit`、`POST /calculate`、`GET /{id}/export/excel|pdf`、`GET /{id}/adjustments` |
| /textbook-reference | 列表、mappings |

## 数据迁移

`20261012New/scripts/migrate_tax_data.py`：原库 `enterprise_tax.db` → 工作台 `mod_tax_*` 表；按业务唯一键（税号/政策文号/企业+年度+季度）判重，幂等；已完成 6 条政策迁移（dry-run → 正式 → 复跑全跳过，三级验证）。

## 前端 Vue 重写（9 视图 / 10 路由）

企业列表、企业表单、税额计算（CalculationRequest 全字段大表单 → 计算过程步骤条 + 优惠标签 + 保存为申报）、申报列表/预览/提交/导出、财报导入（模板下载 + 上传解析结果展示）、统计分析、政策管理、教材参考。原 React/antd 页面未保留（用户决策：重写而非 iframe 过渡）。

## 测试与验收

- pytest 7 例：模块注册、企业 CRUD、小型微利计算口径（300/150/50 + 研发 20 → 应纳税所得额 80 万 × 5% = 4 万）、申报创建/提交、政策/教材端点、Agent 工具零注册
- 注意：原 CLI 口径中 `total_salary` 参与福利/教育/工会限额计算，测试与冒烟均以明确口径写死预期值
- 真实服务冒烟：企业创建 → 计算 → 结果正确；冒烟企业数据已清理
