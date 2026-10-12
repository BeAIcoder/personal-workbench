# finance_invoice 发票处理 · 开发记录（2026-10-12）

> 位置：`backend/app/modules/finance_invoice/` + `frontend/src/modules/finance-invoice/`
> 级别：L0（引擎直挂）｜ 引擎：`01-财税自动化/发票工具/发票批量重命名增加项目/`（invoice_processor 包）

## 功能

上传数电发票 PDF / ZIP → 字段提取（购方/销方/金额/税额/发票号/开票日期/专普票）→ 冲红配对与重复去重 → 人工修正项目归类 → 按规则重命名归档 + 清单 xlsx + ZIP 下载。

## API（/api/finance/invoice）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /engine/status | 引擎可用性自检（含解析到的引擎根目录） |
| POST | /batches | 上传建批次并解析（多个 PDF 或一个 ZIP） |
| GET | /batches、/batches/{id} | 列表 / 详情（含明细） |
| PATCH | /items/{id} | 人工修正项目归类 / 备注 |
| POST | /batches/{id}/export | 重命名复制 + 清单 xlsx + ZIP 打包 |
| GET | /batches/{id}/download | ZIP 下载 |

## 表

- `mod_invoice_batch`：状态机 uploaded → parsed → exported；文件数/解析/失败/重复/红冲计数
- `mod_invoice_item`：发票全字段 + 项目归类 + 冲红配对指针 + 修正标记

## 前端

批次列表（状态徽章 + 上传对话框 + 引擎不可用警示）与批次详情（行内编辑项目/备注 + 导出 + 下载）。

## 测试与验收

- pytest：文本解析/去重/冲红配对单测（mock 提取器，环境无 PDF 生成库不造 PDF）、上传状态机、503 降级——全绿
- Agent 工具：`invoice_batch_stats`（只读）
- 已知限制：仅文本型 PDF；扫描件需人工。真实地址关键词读原项目 config.local.yaml（只读）
