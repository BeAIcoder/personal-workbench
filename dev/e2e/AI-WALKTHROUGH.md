# AI 浏览器走查验收（人工验收层）

对「模拟人工操作」的第二条腿：**AI 控制真实浏览器，像人一样点击验收**。与 Playwright 脚本
（`e2e_recon/interactions/edge.py`，确定性回归主干）分工互补——脚本管"每条路都能走通"，
本层管"人点起来顺不顺、文案对不对、确认框出没出"。

## 什么时候跑

- 每轮修复/发版前（尤其改了前端交互、文案、菜单后）
- Playwright 全绿但涉及弹窗/悬停/徽标等脚本难断言的改动时
- 新页面上线后的一次性走查

## 谁来跑

任何能操作浏览器的 AI（本仓库的协作方式）：

| 通道 | 说明 |
|---|---|
| desktop browser MCP（Kimi Code 内置） | AI 看截图 + 元素快照，点击/悬停/输入；用户可在侧栏实时观看 |
| Kimi 网页桥（WebBridge 技能） | 驱动用户真实浏览器，带登录态的页面也能验 |
| Playwright codegen 人工辅助 | 人工主导、AI 记录 |

## 怎么跑（标准流程）

1. **确认服务在跑**：`curl -s http://127.0.0.1:8000/api/health` 返回 `version` 即就绪
   （没起就双击 `启动工作台.bat`）。
2. **对照验收集逐项点**：清单即 `ai_walkthrough.py` 里的 `CHECKLIST`（当前 12 项，
   覆盖菜单结构、技能包页、专家团文件四个确认/提示、AI 助手设置三处状态、调试中心三页）。
   每项：导航到页面 → 元素/视觉快照定位 → 点击/悬停 → 快照核对预期。
3. **记录结果**：全部通过 → `python ai_walkthrough.py`；有失败 →
   `python ai_walkthrough.py --fail AI-VERIFY-004:tooltip 没弹出 --note "说明"`
   （`--fail` 可重复）。失败项会**自动建/更新问题**（source=e2e、P1），进「问题记录」跟进。
4. **看结果**：调试中心 → 测试运行页出现一条 `ai-verify` 运行档案，12 项逐条列明。

## 归档格式

与 Playwright 完全同构：`backend/data/debug/runs/<run_id>/result.json`
（run_id 形如 `20261013-175354_ai-verify`），经 `debug_reporter.save_run()` 落库，
调试中心「测试运行」页直接可查。

## 维护约定

- 新功能上线/旧交互调整时，同步增删 `CHECKLIST` 条目（ID 顺序递增，name 写清验收点）
- 验收项要选"脚本断言不了或断言成本高"的：确认框、悬停提示、徽标状态、菜单结构、空态文案
- 纯接口/渲染回归留给 `e2e_*.py`，不要往本清单里堆

## 相关

- `debug_reporter.py`（归档共用实现）、调试中心 spec：仓库根 `spec.md` §4.7
- 已同步技能（用户级）：`Agent浏览器`、`网站体验测试`、`网页界面准则`、`屏幕截图`
