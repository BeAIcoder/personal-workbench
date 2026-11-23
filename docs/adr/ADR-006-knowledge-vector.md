# ADR-006：知识库检索采用 FTS5 + 可选 sqlite-vec 混合

> 日期：2026-11-17 ｜ 状态：已生效 ｜ 取代：ADR-003（BM25 不引向量库）

## 背景

ADR-003 在 2026-10-12 决定“RAG 用 bigram + BM25，不引入 embedding/向量库”。当时的约束是：

- 单用户笔记规模小；
- 没有可用的 embedding 端点；
- 希望零新增依赖、完全离线。

2026-11-05 的 v1.18 知识库改变了前提：

- 文档库需要处理制度/手册/软件手册，语义泛化比笔记更重要；
- 已支持配置 embedding 端点（含独立 Key，禁止复用聊天 Key）；
- `sqlite-vec` 可作为可选扩展，缺失时自动降级关键词。

## 决策

知识库检索采用**混合检索**：

1. **FTS5 关键词**：中文 bigram + `unicode61`，作为默认/降级路径；
2. **sqlite-vec 向量**：可选；embedding 维度变更必须重建向量索引；
3. **RRF 融合**：文档/向量/笔记三路结果融合，返回带出处（文档名/版本/章节/页码）的结果；
4. **缺失降级**：`sqlite-vec` 缺失/加载失败时只走关键词，不阻塞启动；
5. **OCR 可选**：扫描件只在“单页文本 <20 字且无文本层”时尝试，失败跳过。

## 后果

- 接受：向量索引需要额外存储/重建成本；embedding Key 必须与聊天 Key 隔离；维度变更需重建。
- 保留：BM25/关键词路径，保证离线/缺依赖可用。
- 不再成立：ADR-003 的“不引入向量库”结论；其“bigram 对 2 字中文词有效”的经验仍有效。

## 相关

- `backend/app/knowledge/index.py`（FTS5/vec0）
- `backend/app/knowledge/embedding.py`（Key 隔离/维度）
- `backend/app/knowledge/retrieval.py`（混合检索）
- `backend/app/knowledge/tokenize.py`（bigram）
- `docs/ARCHITECTURE.md` §3/§5
