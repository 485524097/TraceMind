# TraceMind 项目开发规范

## 项目定位

TraceMind 是一个面向中文开发者的、本地优先、答案可追溯的 AI 个人知识库。

主要解决：

1. 技术文档和代码资料分散
2. AI 对话无法长期沉淀
3. 陌生代码项目难以理解
4. AI 回答缺少可信引用
5. 历史问题和解决方案难以复用

## MVP 核心能力

1. 知识库管理
2. 文件导入和管理
3. Markdown、PDF 和代码文件解析
4. 文档与代码 Chunking
5. 全文检索
6. 向量检索
7. 混合检索
8. Reranker
9. RAG 问答
10. 文件、页码、章节和代码行级引用
11. 对话历史
12. 多模型配置
13. 检索效果评测

## 技术方向

### Backend

- Python 3.12
- FastAPI
- Pydantic v2
- SQLAlchemy 2
- Alembic
- PostgreSQL
- Redis
- Celery
- Qdrant

### Frontend

- Vue 3
- TypeScript
- Vite
- Element Plus

### Deployment

- Docker Compose

## 开发原则

1. 优先进行最小必要修改。
2. 不修改与当前任务无关的模块。
3. 不为了展示技术而引入复杂组件。
4. API 层不得直接编写复杂业务逻辑。
5. 业务逻辑放在 Service 层。
6. 数据访问通过 Repository 层管理。
7. ChatModel、Embedding 和 Reranker 必须使用 Provider 抽象。
8. 业务代码不得绑定单一模型供应商。
9. 引用信息必须来自 Document 和 Chunk 元数据，不得在回答生成后猜测。
10. 所有配置通过环境变量管理。
11. 不得提交 API Key、密码、公司数据或私人敏感资料。
12. 数据库模型变化必须添加 Alembic migration。
13. 新功能必须添加相应测试。
14. 修复 Bug 时优先补充回归测试。
15. 修改前先阅读现有代码和文档。
16. 不确定第三方接口时，应查阅官方文档，不得虚构 API。
17. 完成任务后必须运行相关测试和静态检查。
18. 不要未经允许扩大当前任务范围。

## Git 分支规则

- `main`：稳定、可发布版本
- `develop`：日常开发集成分支
- `feature/*`：正式功能开发
- `experiment/*`：技术学习和试验

禁止直接在 `main` 上开发业务功能。

## 提交信息

使用以下前缀：

- `feat:` 新功能
- `fix:` Bug 修复
- `docs:` 文档
- `test:` 测试
- `refactor:` 重构
- `chore:` 工程配置

## 每次任务完成后输出

1. 修改文件列表
2. 实现内容
3. 执行过的命令
4. 测试和检查结果
5. 遗留问题
6. 下一步建议

## MVP 暂不实现

- 完整知识图谱
- 多用户权限系统
- 手机客户端
- 插件市场
- 复杂 Markdown 编辑器
- 自动研究 Agent
- 自动个人画像
- 音频和视频解析
