# 固定检索评测人工清单 v1

> 本清单由机器可读 JSONL 数据集生成。只能上传配套语料，不能上传本清单。

| 编号 | 问题 | 类型 | 难度 | 预期章节 | 预期关键证据 | Top 5 通过条件 | 实际结果 | 是否通过 |
|---|---|---|---|---|---|---|---|---|
| ret-001 | 这个系统的实时输送周期和责任汇总周期有什么区别？ | semantic | easy | 1. 系统边界与运行周期 | 调度窗用于实时输送，默认一分钟；结算窗用于汇总责任与损耗，默认三十分钟。 | Top 5 命中全部 1 条 required 证据 |  |  |
| ret-002 | 哪个组件负责整理设备观测，它会不会直接预测用量？ | semantic | easy | 2. 晨潮采集器 | 它校验观测时间、设备序列和单位，将同一潮拍的重复包合并为一个不可变快照。 | Top 5 命中全部 1 条 required 证据 |  |  |
| ret-003 | 需求估算模块产出什么，历史数据不够时怎么处理？ | semantic | medium | 3. 霁光预测器 | 历史不足时，预测器返回保守曲线并标记低可信来源。 | Top 5 命中全部 1 条 required 证据 |  |  |
| ret-004 | 同等级对象如何避免一直由同一个对象优先拿到能量？ | semantic | medium | 4. 脉轮协调器 | 它先保障生命维持舱，再处理农圃与工坊；同级对象按照上个结算窗的欠供比例轮转。 | Top 5 命中全部 1 条 required 证据 |  |  |
| ret-005 | 主输送线路装不下全部配额时，系统能否拆成多条线路？ | semantic | easy | 5. 雾桥路由器 | 当主路径的剩余容量低于配额时，可拆分到两条互不共享脆弱节点的路径。 | Top 5 命中全部 1 条 required 证据 |  |  |
| ret-006 | 追踪模块能证明什么，又不能替代什么？ | semantic | medium | 7. 银弦审计器 | 审计器只证明系统当时使用了哪些输入与规则，不替代监控告警，也不会回滚现场动作。 | Top 5 命中全部 1 条 required 证据 |  |  |
| ret-007 | tide.collect.late-grace-seconds 的默认值是多少？ | config_exact | easy | 2. 晨潮采集器 | `tide.collect.late-grace-seconds` 控制迟到包宽限，默认值为 6。 | Top 5 命中全部 1 条 required 证据 |  |  |
| ret-008 | dawn.forecast.horizon-windows 默认配置 | config_exact | easy | 3. 霁光预测器 | `dawn.forecast.horizon-windows` 决定预测跨度，默认 12 | Top 5 命中全部 1 条 required 证据 |  |  |
| ret-009 | mist.route.max-hops 最大跳数默认多少？ | config_exact | easy | 5. 雾桥路由器 | 配置项 `mist.route.max-hops` 默认 7，限制单条路径的最大桥段数。 | Top 5 命中全部 1 条 required 证据 |  |  |
| ret-010 | silver.audit.retention-days 会不会改变原始观测的保留时间？ | config_exact | medium | 7. 银弦审计器 | `silver.audit.retention-days` 默认 45，只影响本地审计记录保留期，不影响原始观测快照的保留策略。 | Top 5 命中全部 1 条 required 证据 |  |  |
| ret-011 | SLR-RTE-423 表示什么，应该先查哪里？ | error_code | easy | 5. 雾桥路由器；11. 路径计算与容量处理 | `SLR-RTE-423`，含义是“输送路径不可达”；处理 `SLR-RTE-423` 时先核对桥段是否被拓扑筛选移除，再检查 `mist.route.max-hops`。 | Top 5 命中全部 2 条 required 证据 |  |  |
| ret-012 | SLR-FOR-207 能靠等待更多历史数据自动恢复吗？ | error_code | medium | 16. 预测退化与恢复 | 单位不兼容的 `SLR-FOR-207` 不会因等待而恢复。运维人员必须修正观测单位映射 | Top 5 命中全部 1 条 required 证据 |  |  |
| ret-013 | SLR-STO-519 出现后最高优先级目标还能强制放电吗？ | error_code | medium | 12. 储能支援与下限保护 | 紧急授权只绕过荷电下限，不绕过温差冻结。出现 `SLR-STO-519` 时，即使生命维持舱具有最高优先级，也必须等待电芯温差恢复 | Top 5 命中全部 1 条 required 证据 |  |  |
| ret-014 | 配额咋轮？ | short_query | medium | 4. 脉轮协调器 | 它先保障生命维持舱，再处理农圃与工坊；同级对象按照上个结算窗的欠供比例轮转。 | Top 5 命中全部 1 条 required 证据 |  |  |
| ret-015 | 路不通先看啥 | short_query | hard | 11. 路径计算与容量处理 | 先核对桥段是否被拓扑筛选移除，再检查 `mist.route.max-hops`。 | Top 5 命中全部 1 条 required 证据 |  |  |
| ret-016 | 迟到包咋办 | short_query | medium | 15. 观测包迟到处理 | 迟到不超过 `tide.collect.late-grace-seconds` 的观测包可以重开当前潮拍快照；超过宽限的包只进入旁路记录并产生 `SLR-COL-104` | Top 5 命中全部 2 条 required 证据 |  |  |
| ret-017 | 要解释一个目标最终获配额度的原因，需要结合哪些信息？ | multi_evidence | hard | 3. 霁光预测器；4. 脉轮协调器；10. 配额生成与批准流程 | 霁光预测器读取最近二十四个调度窗的快照，并结合虚构节律表推算未来十二个窗口。；它先保障生命维持舱，再处理农圃与工坊；同级对象按照上个结算窗的欠供比例轮转。 | Top 5 命中全部 2 条 required 证据 |  |  |
| ret-018 | 路径容量不够时，附近储能是否允许支援要同时判断什么？ | multi_evidence | hard | 3. 霁光预测器；6. 余辉储能器；12. 储能支援与下限保护 | 霁光预测器读取最近二十四个调度窗的快照，并结合虚构节律表推算未来十二个窗口。；`afterglow.storage.floor-percent` 默认 22，表示常规调度不得突破的最低荷电比例 | Top 5 命中全部 2 条 required 证据 |  |  |
| ret-019 | 某目标能量不足时，应该按什么跨模块顺序定位？ | multi_evidence | hard | 4. 脉轮协调器；5. 雾桥路由器；17. 运维检查顺序 | 协调器不计算物理输送路线，因此“获得配额”不代表某条雾桥必然可用。；前者应检查拓扑与禁行清单，后者应调整配额或请求储能支援。 | Top 5 命中全部 2 条 required 证据 |  |  |
| ret-020 | 预测范围和配额预留同时调整时，怎样避免审计上分不清变化来源？ | multi_evidence | hard | 10. 配额生成与批准流程；18. 安全变更窗口 | 霁光预测器给出的需求区间和本节的优先级、欠供轮转及预留规则；先保留旧预测版本完成当前配额批准，再让新跨度生成下一版本需求，最后应用新的预留比例。 | Top 5 命中全部 2 条 required 证据 |  |  |
| ret-021 | 设备观测快照和决策追踪快照有什么本质差别？ | concept_disambiguation | medium | 8. 模块相似概念辨析 | 采集快照保存设备观测，审计快照保存决策来源。 | Top 5 命中全部 1 条 required 证据 |  |  |
| ret-022 | 储能动作方案和目标分配上限是同一回事吗？ | concept_disambiguation | medium | 8. 模块相似概念辨析；6. 余辉储能器 | 配额草案约束目标对象，储能计划约束电芯动作。 | Top 5 命中全部 1 条 required 证据 |  |  |
| ret-023 | 星门身份令牌的有效期和自动续签窗口是多少？ | unanswerable | hard | 无明确答案 | 观察返回内容，不自动判定 | observational，不计入默认回归失败 |  |  |
| ret-024 | 天穹外部气象接口失败后采用几秒的指数退避？ | unanswerable | hard | 无明确答案 | 观察返回内容，不自动判定 | observational，不计入默认回归失败 |  |  |

## 汇总

- 总问题数：24
- 通过数：
- 未通过数：
- Hit@1：
- Recall@5：
- MRR@5：
- 多证据完整命中率：
- 无答案观察结果：
- P50：
- P95：
