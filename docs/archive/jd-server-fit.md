# 服务端 AI 实习 JD 对齐笔记

目标方向：服务端系统 / AI 基础设施实习。

这份文档只记录当前项目已经实现或正在实现的能力，不把计划中的功能当成已有经历。

## 1. JD 能力与项目证据

| JD 能力 | 项目对应位置 | 当前状态 | 面试可讲重点 |
|---|---|---|---|
| 服务端方案设计、开发、维护 | `src/service/app.py`、`src/cli/main.py`、`src/github/client.py` | 支持 | CLI、FastAPI、GitHub API、报告输出的请求链路 |
| 高质量设计与编码 | `src/models.py`、`src/reviewer/engine.py`、`src/reviewer/model_payload.py` | 支持 | Pydantic schema、输入校验、JSON 解析 fallback、报告验证 |
| 重点/难点问题攻坚 | `src/reviewer/provider.py`、`src/agent/runner.py`、`src/analyzer/cross_file.py` | 支持 | Provider 失败降级、规则噪声、跨文件分析误报 |
| 技术调研与新技术引入 | `src/agent/`、`src/analyzer/`、`src/reviewer/` | 支持 | Agent orchestration、Token Budget、AST、跨文件分析 |
| Python / 面向对象 | 全项目 Python；`Settings`、`ReviewAgentRunner`、`GitHubClient`、Pydantic models | 支持 | 类职责、Protocol、依赖注入和可测试性 |
| OpenAI-compatible API 接入 | `src/reviewer/provider.py`、`src/utils/config.py` | 支持 | `/chat/completions`、API Key、Base URL、Model、HTTP 错误分类 |
| 上下文限制 / Token Budget | `src/analyzer/context_builder.py` | 支持 | 预算、截断、跳过文件、上下文完整性 |
| 流式传输 SSE | `src/service/app.py`、`src/agent/runner.py`、`docs/sse-review-stream.md` | 支持 | FastAPI `text/event-stream` 输出 Agent 步骤与最终报告；含事件协议、耗时、错误事件和断连边界 |
| RAG / Chunking / Embedding / Rerank | 主要在 EchoForge，不在本项目 | 由另一项目支持 | 两个项目不要混写成一个系统 |
| Vector DB / 长期记忆 | EchoForge 的 ChromaDB、Redis、记忆写入 | 由另一项目支持 | 结合 RAG 项目回答，不强行塞进 PR Review |
| MCP / 多模态 | 当前未实现 | 缺口 | 只能作为技术调研方向 |
| 每周 4 天、4 个月以上 | 求职条件，不属于代码证据 | 待确认 | 面试/投递前直接确认可用时间 |

## 2. 当前已完成的可靠性改造

### Provider 兼容与诊断

`src/reviewer/provider.py` 当前具备：

- OpenAI-compatible `/chat/completions` 调用；
- API Key、Base URL、Model 和 timeout 配置；
- 401、429、5xx 和其他 4xx 的分类；
- 保留 provider 返回的安全错误摘要；
- 遇到不支持 `response_format=json_object` 的 400 时，重试一次纯 Chat Completions 请求；
- 将详细错误传给降级报告，便于判断是模型名、Base URL、参数兼容性还是认证问题。

### 规则分析质量

`src/analyzer/cross_file.py` 当前修复了两类噪声：

- 同一个函数在同一文件的多个 patch hunk 中重复出现时，不再错误判定为跨文件重构；
- 汇总型跨文件发现不再使用空文件路径。

`src/reviewer/engine.py` 当前将低置信度或没有具体文件位置的规则命中保留在审计字段中，但不再直接生成用户可见建议或抬高总体风险等级。

## 3. 面试八股题目与项目落点

### 服务端架构

1. 这个项目从 PR 进入到报告生成经过哪些模块？
   - `GitHubClient` 获取 PR 和 changed files；
   - analyzer 生成规则和跨文件信号；
   - context builder 组装上下文并控制 token；
   - reviewer/provider 调用模型；
   - engine 解析、过滤、验证并输出报告。

2. 为什么同时保留 CLI 和 FastAPI？
   - CLI 适合 GitHub Actions 和本地自动化；
   - FastAPI 适合 Web Console 和后续服务化；
   - 核心分析逻辑放在模块层，入口层只负责参数和协议适配。

3. 如果模型服务不可用，系统如何保证仍然有结果？
   - Provider 捕获超时、认证、限流、服务端和请求错误；
   - AI 失败时降级到规则报告；
   - 报告标记 `execution_status`、`degradation_reason` 和 `report_confidence`。

### Python 与工程质量

4. 为什么使用 Pydantic model？
   - 约束跨模块数据结构；
   - 校验模型输出和报告字段；
   - 让 CLI、Web、JSON、Markdown 共享同一份领域模型。

5. 如何测试外部 API 依赖？
   - 用 mock GitHub client 隔离网络；
   - 用 `respx` 模拟 provider HTTP 响应；
   - 覆盖成功、400、认证失败、降级和 JSON 解析失败路径。

6. 如何避免分析器自身修改后导致 CI 失效？
   - workflow 在 base branch 上运行稳定版本 reviewer；
   - 另行检查 PR head 的 Python syntax；
   - 通过 pytest、ruff 和 VS Code extension build 做质量门禁。

### LLM 服务接入

7. 为什么 HTTP 400 不一定是 API Key 错误？
   - 无 Key 时系统应在请求前降级；
   - 401 通常更接近认证失败；
   - 400 还可能来自模型不存在、Base URL 不匹配或不支持 `response_format`；
   - 因此必须保留 provider 返回的错误正文，而不是只记录状态码。

8. 如何接入不同的大模型服务？
   - 使用统一的 OpenAI-compatible Provider 接口；
   - 通过 `OPENAI_BASE_URL` 和 `REVIEW_MODEL` 切换服务；
   - 对不同服务的 JSON 输出能力做 capability probe 或兼容 fallback。

9. 上下文太长时怎么办？
   - 统计 token；
   - 按优先级保留规则命中文件和核心 patch；
   - 跳过 lockfile/生成文件；
   - 超预算时截断并在报告中标记上下文不完整。

### 规则与 Agent 协同

10. 规则引擎的结果是否会影响 Agent？
    - AI 成功时，规则 findings 会进入 Prompt 的 `Rule findings` 区域；
    - Prompt 要求模型逐条确认或驳回；
    - AI 失败时不会经过 Agent 二次判断，直接走 rule-only，因此规则噪声会暴露出来。

11. 为什么路径规则不能直接判定 HIGH？
    - 文件路径只能说明“可能需要关注”，不能证明具体缺陷；
    - 当前项目已将 CI/reviewer 路径规则改成较低置信度信号；
    - 后续应让 Agent 验证后再把 signal 转成用户建议。

12. 如何处理规则误报？
    - 规则输出 signal，而不是最终结论；
    - 使用 diff 上下文做二次验证；
    - 确认后进入 suggestions，驳回后进入 dismissed signals；
    - 记录规则命中、Agent 判断和最终展示结果，方便复盘。

## 4. 当前不足与后续优先级

1. Provider 目前可以诊断和兼容部分 400，但还没有多 Provider 自动切换。
2. 规则结果目前会进入 Agent Prompt，但尚未拆成独立的 `verify_signals` Agent tool。
3. AI 失败时仍然只能 rule-only，低置信度规则虽已隐藏，但不能替代真实语义审查。
4. SSE 已实现长任务状态与最终报告流；下一阶段可补 cancel token，处理中断时停止底层 Provider 请求。
5. RAG、Vector DB 和长期记忆继续由 EchoForge 承担，不与本项目强行合并。

## 5. 当前验收

```text
uv run pytest -q
96 passed
```

这只能证明本地测试通过，不能等价于线上 Provider 可用或规则误报率已经下降。后续需要用多个 provider 配置和一组人工标注 PR 做对比评测。
