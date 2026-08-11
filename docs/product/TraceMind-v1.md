# TraceMind v1.0

TraceMind 是一个本地优先、面向个人长期学习与技术积累的可追溯知识系统。

## 核心使用循环

导入资料 → 观察解析与索引 → Ready → 提问 → 查看 RAG Pipeline → 验证 Citation →
保存为知识 → 在知识列表与知识图谱中继续学习。

v1.0 的成功标准不是功能数量，而是用户愿意在真实学习过程中每天打开它。文档、
PDF、Markdown 和代码文件都作为通用技术文本处理；TraceMind 不承担代码智能、仓库理解、
Agent 或 GraphRAG 的职责。

## v1.0 能力边界

- 文件与普通多文件上传，真实上传字节进度。
- Parse、Chunk、Embed/Index 与 Ready 状态可见。
- Dense + BM25 + RRF，可选 Reranker，引用来自真实 Chunk。
- 确定性的 Direct/RAG 路由：只有精确白名单寒暄绕过检索，不确定问题进入 RAG。
- Conversation、KnowledgeEntry、Evidence snapshot 与派生 Knowledge Map。
- 中文主界面；Dense、BM25、RRF、Reranker、Citation 等专业名词保留。

## 明确不做

Java/Python/JavaScript AST、Symbol Scope、调用图、目录级代码仓库导入、Agent、图数据库、
GraphRAG 和自动改代码均不属于 v1.0。

## 数据边界

文件、结构化数据、向量索引和知识快照默认保留在本机。模型 Provider 由环境配置决定：若使用
远程 LLM，问题、必要会话历史和本次检索到的 Source 内容会发送给该 Provider；若要求资料不
离开本机，应配置本地 OpenAI-compatible LLM endpoint。凭据只通过环境变量提供，不进入文档、
日志或 Git。
