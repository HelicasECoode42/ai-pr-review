# SSE 审查进度流

## 接口

`POST /api/analyze/stream` 接收与 `/api/analyze` 相同的 JSON 请求，返回
`Content-Type: text/event-stream`。它不会把 API Key、原始 Prompt 或完整 diff
写入事件；只发送脱敏步骤摘要和最终报告。

```json
{
  "repo": "HelicasECoode42/ai-pr-review",
  "pr_number": 36,
  "language": "zh",
  "use_ai": true,
  "agent_mode": "one_shot_ai"
}
```

## 事件协议

| event | 何时发送 | 关键字段 |
|---|---|---|
| `started` | HTTP 流已建立 | `repo`, `pr_number`, `transport` |
| `step_started` | Agent 工具开始 | `index`, `tool` |
| `step_completed` | 工具成功 | `tool`, `duration_ms`, `observation_summary` |
| `step_failed` | 工具失败 | `tool`, `duration_ms`, `error` |
| `strategy_selected` | 已选择 AI / rule-only 策略 | `strategy`, `reason`, `files_count` |
| `complete` | 审查完成 | `report`, `markdown`, `agent`, `duration_seconds` |
| `error` | 无法完成审查 | `message` |

示例帧：

```text
event: step_completed
data: {"index":2,"tool":"scan_risks","duration_ms":18,"status":"success"}

```

## 服务端实现与边界

- `ReviewAgentRunner` 用可选 `progress_sink` 发出步骤事件，不依赖 FastAPI。
- FastAPI 在后台线程运行同步 GitHub/Provider 调用，用线程安全队列把事件交给 SSE generator。
- 客户端断连不会中断正在进行的同步 Provider 请求；这是当前 Provider 接口的边界。下一阶段可加入 cancel token 与 HTTP client cancellation。
- `complete` 事件已有每个步骤的 `duration_ms`，可用于计算 GitHub、上下文、模型与 Critic 的耗时占比。

## 本地验证

```bash
uv run uvicorn src.service.app:app --reload

curl -N -X POST http://127.0.0.1:8000/api/analyze/stream \
  -H 'Content-Type: application/json' \
  -d '{"repo":"HelicasECoode42/ai-pr-review","pr_number":36,"language":"zh","agent_mode":"one_shot_ai"}'
```

`curl -N` 关闭缓冲，终端应先显示 `started` 与各 Agent 步骤，最后显示 `complete`。
