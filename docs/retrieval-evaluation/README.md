# TraceMind 固定检索评测

本目录提供一套可重复运行的中文检索评测资产，用于在 Chunking、检索策略或索引实现调整前后比较 Dense 与 Hybrid 的召回质量。语料、问题、证据锚点和指标定义都固定在版本库中，避免每次人工测试临时换问题而失去可比性。

> **只能把 `synthetic_retrieval_corpus_v1.md` 上传到 TraceMind。**
>
> **禁止上传 JSONL、Manifest、人工清单、自动评测报告或 Baseline。** 这些文件包含预期证据，会污染检索结果并导致评测失真。

## 文件分工

- `synthetic_retrieval_corpus_v1.md`：完全虚构的技术手册，是唯一需要上传的文件。
- `backend/evals/retrieval/datasets/synthetic_retrieval_v1.jsonl`：机器可读 Gold Dataset。
- `backend/evals/retrieval/datasets/synthetic_corpus_manifest_v1.json`：语料哈希和检索配置快照。
- `synthetic_retrieval_checklist_v1.md`：由 JSONL 自动生成的人工检查表。
- `backend/evals/retrieval/reports/`：本地评测输出目录，不上传到知识库。
- `backend/evals/retrieval/baselines/`：人工确认后的回归基线，不上传到知识库。

语料与 Gold Dataset 必须分开，因为语料模拟用户真正拥有的技术资料，Gold Dataset 描述评测问题和预期证据。人工清单适合逐项观察，机器数据适合自动计算；二者由同一份 JSONL 生成，避免内容漂移。

## 数据划分

固定数据集共有 24 条问题，其中 dev 18 条、test 6 条。dev 可用于理解失败原因和有限调参；test 只用于最终复测，不应根据 test 的逐题结果反复调整检索参数。本 README 不公开 test split 的逐题预期结果。

Gold Evidence 使用文档名、Markdown 章节、真实行号和连续原文锚点标识，不把 `chunk_id` 当作唯一证据 ID。Chunking 参数变化后，Chunk ID 和边界可能变化，但原始文档事实仍可通过行号区间、章节和原文锚点稳定匹配。

## 获取 knowledge_base_id 和 document_id

1. 在 TraceMind 创建一个专用评测知识库。
2. 只上传 `synthetic_retrieval_corpus_v1.md`。
3. 等待解析和索引成功。
4. 浏览器地址栏中的知识库路径可以得到 `knowledge_base_id`。
5. 文档列表请求或浏览器开发者工具中的文档 API 响应可以得到 `document_id`。
6. Runner 强制要求 `document_id`，不会默认搜索整个知识库。

建议该知识库只包含这一个文件。Runner 也会校验所有返回结果的 `document_name`，一旦检索到其他文档便将该 Case 记为无效响应。

## 校验数据集

Windows CMD：

```cmd
cd backend

uv run --no-sync python -m evals.retrieval.validate_dataset ^
  --corpus ../docs/retrieval-evaluation/synthetic_retrieval_corpus_v1.md ^
  --dataset evals/retrieval/datasets/synthetic_retrieval_v1.jsonl ^
  --manifest evals/retrieval/datasets/synthetic_corpus_manifest_v1.json ^
  --checklist ../docs/retrieval-evaluation/synthetic_retrieval_checklist_v1.md
```

重新生成清单时使用 `--write-checklist` 替代 `--checklist`。校验器会检查 JSONL、Case ID、问题分布、dev/test 数量、可回答约束、章节、真实行号、原文锚点、无答案关键词、Manifest SHA-256 和清单同步状态。

## 运行 Dense 与 Hybrid 对比

确保本地 Backend 正在 `127.0.0.1:8000` 运行，然后执行：

```cmd
cd backend

uv run --no-sync python -m evals.retrieval.runner ^
  --base-url http://127.0.0.1:8000 ^
  --knowledge-base-id <KNOWLEDGE_BASE_ID> ^
  --document-id <DOCUMENT_ID> ^
  --dataset evals/retrieval/datasets/synthetic_retrieval_v1.jsonl ^
  --manifest evals/retrieval/datasets/synthetic_corpus_manifest_v1.json ^
  --strategies dense hybrid ^
  --top-k 5 ^
  --split all ^
  --output evals/retrieval/reports/baseline_v1
```

Runner 只调用 TraceMind 已有的 Dense 与 Hybrid HTTP API，不复制 Dense、BM25 或 RRF 算法，不调用 RAG、LLM、Reranker、外部 API 或 Qdrant Cloud。单条请求失败会写入该 Case 的报告，后续 Case 仍继续执行。

输出包括：

```text
<output>/
├── dense.json
├── dense.md
├── hybrid.json
├── hybrid.md
└── comparison.md
```

## 指标含义

- **Recall@5**：Top 5 命中的正相关 Gold Evidence 数量占全部正相关证据的比例，是主指标。
- **MRR@5**：第一条相关结果排名的倒数，越接近 1 表示核心证据出现越早。
- **nDCG@5**：根据 relevance 0/1/2 衡量前五名排序质量，核心证据排在前面得分更高。
- **All-required@5**：一个问题的全部 `required=true` 证据是否都进入 Top 5，重点观察多证据问题。
- **Hit@1 / Hit@5**：前一条或前五条中是否至少出现一条相关证据。
- **Precision@5**：前五个位置中相关检索结果的比例。
- **P50 / P95**：单次检索耗时的中位数和第 95 百分位。

同一个检索结果和同一个 Gold Evidence 都不会重复计分。`k<=0` 会被明确拒绝，空结果安全计为未命中。

## 保存与比较基线

首次运行产生的是 baseline candidate。先人工查看 `hybrid.md`、`dense.md` 和 `comparison.md`，确认语料只上传一次、索引完整且 Gold 匹配合理，再显式复制基线：

```cmd
copy /-Y evals\retrieval\reports\baseline_v1\hybrid.json ^
  evals\retrieval\baselines\hybrid_v1.json
```

`/-Y` 会在覆盖已有文件前询问。评测工具不会自动覆盖正式 baseline。

后续回归比较：

```cmd
uv run --no-sync python -m evals.retrieval.runner ^
  --base-url http://127.0.0.1:8000 ^
  --knowledge-base-id <KNOWLEDGE_BASE_ID> ^
  --document-id <DOCUMENT_ID> ^
  --dataset evals/retrieval/datasets/synthetic_retrieval_v1.jsonl ^
  --manifest evals/retrieval/datasets/synthetic_corpus_manifest_v1.json ^
  --strategies hybrid ^
  --top-k 5 ^
  --split all ^
  --baseline evals/retrieval/baselines/hybrid_v1.json ^
  --fail-on-regression ^
  --output evals/retrieval/reports/current
```

默认质量回归阈值为 Recall@5 下降超过 0.02、MRR@5 下降超过 0.03、All-required@5 下降超过 0.02、Hit@1 下降超过 0.05。P95 增加超过 50% 只产生警告。阈值可通过 Runner 参数调整，质量回归配合 `--fail-on-regression` 时进程返回非零退出码。

## 在检索改造后复测

修改 Chunking、Parent-Child Retrieval 或 Query Rewrite 后，应使用同一份语料文件和 JSONL：

1. 记录原有 Manifest 和 Baseline。
2. 用新实现重新解析并索引同一个虚构语料文档。
3. 运行 dev split 定位实现问题。
4. 参数冻结后运行 all 或 test split。
5. 比较 Recall@5、MRR@5、nDCG@5、All-required@5 与延迟变化。
6. 检查失败 Case 的文档、章节、行号、Content preview 和缺失 required Evidence。

## 无答案 Case

当前两个无答案问题只输出 `returned_count`、Top1 score、文档、章节和内容预览，并标记为 `observational`。Hybrid RRF 分数不是跨查询校准后的相关性概率，第一版没有可靠阈值，因此无答案 Case 不参与默认 Recall、MRR、nDCG 或回归失败判断。

## 自动评测不能替代的人工检查

自动指标不能判断语义答案是否完整、文档解析是否保持排版、引用是否便于用户理解，也不能证明无答案结果足够安全。人工仍需检查多证据问题是否真的覆盖不同章节、相似概念是否被混淆、短查询是否命中偶然关键词、无答案 Top1 是否具有误导性，以及实际界面展示的文件名、章节和行号是否正确。
