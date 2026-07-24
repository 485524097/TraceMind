# Citation-grounded Streaming RAG

TraceMind 当前提供单轮、无状态的引用约束 RAG。它默认使用 Dense + BM25 RRF Hybrid
Search 召回当前版本的 active generation，不包含对话历史、Agent、Tools 或 Reranker。

## 请求链路

`POST /api/v1/knowledge-bases/{knowledge_base_id}/rag/stream` 接收问题、可选语言和
Document ID。服务复用 `DocumentIndexingService.hybrid_search()`。Dense Prefetch 使用
现有阈值，BM25 Prefetch 和最终 RRF 不使用固定阈值；没有来源时返回 `no_answer`，
不会调用 LLM。RRF score 只表示融合排名，不表示回答置信度。

Context Builder 保持检索顺序并按 Chunk ID 去重，只加入完整 Chunk。后续 Chunk超过
`RAG_MAX_CONTEXT_CHARS` 时跳过，不从正文中间截断。Prompt 使用
`json.dumps(..., ensure_ascii=False)` 序列化来源，原始 Chunk 正文及全部引用元数据保持
不变。

Sources 被视为不可信数据。System Prompt 明确要求忽略来源中的命令、角色切换、Prompt
和工具调用要求，并禁止依赖模型自身知识补充事实。这是 Prompt Injection 的纵深防御，
不能保证模型永远遵循指令，部署时仍需限制模型和网络权限。

回答使用 `[S1]`、`[S2]` 引用。Streaming Citation Guard 能处理跨 delta 引用并删除
不存在的来源编号。它只验证编号是否属于本次 Sources，不能自动证明每个自然语言事实
都被来源充分支持。没有有效引用时，`done.grounded=false`，前端提示用户核对原始来源。

## SSE 协议

后端使用 FastAPI 原生 `EventSourceResponse` 和 `ServerSentEvent`，事件为：

- `retrieval`：trace ID、来源数量和完整可追溯来源。
- `token`：经过 Citation Guard 的增量正文。
- `no_answer`：知识库没有足够相关信息。
- `done`：结束原因、grounded 状态、引用统计和延迟。
- `error`：流建立后的安全错误，不包含上游正文或配置。

客户端断开或取消时停止消费上游异步流。前端使用 `fetch` POST 和
`eventsource-parser`，不会自动重试，也不使用浏览器 `EventSource`。

## LLM 配置

```dotenv
LLM_BASE_URL=
LLM_API_KEY=
LLM_MODEL=
LLM_TIMEOUT_SECONDS=120
LLM_TEMPERATURE=0.1
LLM_MAX_TOKENS=1200
RAG_RETRIEVAL_LIMIT=5
RAG_MAX_CONTEXT_CHARS=12000
```

`LLM_BASE_URL` 与 `LLM_MODEL` 必须同时配置。Key 可以为空以兼容本地服务；API Key
只在后端传给官方 OpenAI Python SDK，不进入前端、响应或日志。接口需要兼容 OpenAI
Chat Completions streaming API。

未配置 LLM 时应用仍可启动，只有 RAG API 返回 503。

## 测试与手工验收

单元测试使用 Fake Provider，不调用真实模型：

```powershell
cd backend
uv run --no-sync pytest -m "not integration"
```

本机 CUDA PyTorch 属于运行环境，不通过本功能修改项目 Torch 锁定策略。配置真实的
OpenAI-compatible 服务后，可以在文档页面输入问题，确认答案逐步出现、引用按钮定位
到原始 Chunk、停止按钮终止生成，并检查日志不包含问题、来源、答案或密钥。

当前不保存页面刷新前后的问答历史。Dense Search API 仍保留用于检索调试对比；当前
没有 Reranker、Weighted RRF 或检索评测集自动调参。
