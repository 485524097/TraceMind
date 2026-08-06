# Java Symbol Retrieval Evaluation

- Case Pass Rate: 1.0000
- Scope Resolution Accuracy: 1.0000
- Exact Target Recall@5: 1.0000
- Signature Exclusion Accuracy: 1.0000
- Fallback Reason Accuracy: 1.0000
- Negative Trigger Accuracy: 1.0000
- Path Disambiguation Accuracy: 1.0000
- P95 Latency: 46739.19 ms (observational)
- Cleanup: succeeded

## sym-001 · passed

- Query: 解释 demo.UserService#source(String) zzzxqv_direct_scroll_token
- Scope: exact / -
- Failures: none
- Latency: 46739.19 ms

## sym-002 · passed

- Query: Outer.Nested#run(String)
- Scope: exact / -
- Failures: none
- Latency: 2619.95 ms

## sym-003 · passed

- Query: demo.UserService#source
- Scope: exact / -
- Failures: none
- Latency: 2347.19 ms

## sym-004 · passed

- Query: src/main/java/demo/UserService.java 中的 UserService#source(String)
- Scope: exact / -
- Failures: none
- Latency: 2362.61 ms

## sym-005 · passed

- Query: UserService#source(String)
- Scope: exact / -
- Failures: none
- Latency: 2562.84 ms

## sym-006 · passed

- Query: 查看 demo.UserService 构造函数
- Scope: exact / -
- Failures: none
- Latency: 2783.56 ms

## sym-007 · passed

- Query: 查看 demo.Item 构造函数
- Scope: exact / -
- Failures: none
- Latency: 2533.15 ms

## sym-008 · passed

- Query: 用户服务#查询(String)
- Scope: exact / -
- Failures: none
- Latency: 2779.84 ms

## sym-009 · passed

- Query: demo.UserService#missing(String)
- Scope: fallback / not_found
- Failures: none
- Latency: 2371.12 ms

## sym-010 · passed

- Query: UserService#source
- Scope: fallback / ambiguous
- Failures: none
- Latency: 2236.16 ms

## sym-011 · passed

- Query: redis.host
- Scope: none / -
- Failures: none
- Latency: 2179.71 ms

## sym-012 · passed

- Query: demo.UserService.source(String username)
- Scope: exact / -
- Failures: none
- Latency: 2610.87 ms
