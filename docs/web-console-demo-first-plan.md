# Web Console Demo-First 改造方案

## 1. 背景问题

当前 Web Console 的“Try Demo”并不是离线演示模式。

它只是把页面输入框填成一个真实公开 PR：

```text
repo: openclaw/openclaw
pr: 80419
use_ai: false
```

用户点击 Analyze 后，前端仍然会调用：

```text
POST /api/analyze
```

后端仍然会访问 GitHub API 拉取 PR 元信息和 changed files。因此这个 demo 仍然依赖：

- GitHub API 可访问
- GitHub anonymous rate limit 未触发
- 可选的 `GITHUB_TOKEN` 配置正确
- 网络环境稳定

这不适合对外演示。对外演示的核心要求是：

```text
启动服务 -> 打开网页 -> 点击 Try Demo -> 立即看到完整报告
```

不应该要求评委、面试官或第一次运行项目的人先配置 token。

另外，当前项目根目录 `.env` 是空文件：

```text
C:\Users\HelicasE\Desktop\program\ai-pr-review\ai-pr-review\.env
Length: 0
```

如果本地运行时以为 token 已经写入 `.env`，实际服务读不到配置。

## 2. 目标

这次改造目标不是重写 Web Console，也不是改变 Agent Runner。

目标是给 Web Console 增加一个稳定的 demo-first 入口：

- 无 `GITHUB_TOKEN` 可演示。
- 无 `OPENAI_API_KEY` 可演示。
- 无外部 GitHub 请求可演示。
- 无 LLM 请求可演示。
- 真实 PR 分析能力继续保留。
- 真实 PR 分析失败时给出清晰提示，而不是让用户感觉网站坏了。

最终用户体验应该是：

```text
uv run uvicorn src.service.app:app --reload
打开 http://127.0.0.1:8000
点击 Try Demo
看到完整 PR Review 报告
```

## 3. 非目标

本阶段不做以下事情：

- 不内置任何真实 GitHub Token。
- 不提交 `.env`。
- 不要求用户配置 OpenAI / DeepSeek API Key 才能看效果。
- 不引入数据库。
- 不引入登录鉴权。
- 不改 GitHub Actions。
- 不改 VS Code 插件。
- 不改 Stage A / Stage B 的 Agent Runner 代码。
- 不把规则系统继续加重。

## 4. 推荐方案

### 4.1 增加离线 demo report

把一份已存在、可以稳定渲染的历史报告固化为 demo asset。

当前可用候选：

```text
reports/history/openclaw_openclaw_80419_20260706-005906_full.json
```

建议复制为稳定路径：

```text
src/service/demo/openclaw_80419_full.json
```

原因：

- `reports/history` 是运行产物，不适合作为长期稳定 demo 资源。
- `src/service/demo` 语义清晰，方便打包和说明。
- demo 数据随项目提交后，别人 clone 下来即可运行。

### 4.2 增加 `/api/demo-report`

在 `src/service/app.py` 增加只读接口：

```text
GET /api/demo-report
```

返回结构沿用当前历史 full report 的结构：

```json
{
  "report": {},
  "markdown": "...",
  "duration_seconds": 0.2,
  "analyzed_at": "20260706-005906"
}
```

要求：

- 不访问 GitHub。
- 不访问 LLM。
- 只读取本地 demo JSON。
- demo 文件缺失时返回 404，并给出明确错误。

### 4.3 增加 `/api/config`

在 `src/service/app.py` 增加配置状态接口：

```text
GET /api/config
```

只返回布尔状态，不返回任何密钥内容：

```json
{
  "github_token_configured": false,
  "openai_api_key_configured": false,
  "demo_available": true,
  "default_agent_mode": "rule_only"
}
```

这个接口的作用是让前端知道当前运行环境：

- 是否有 GitHub token。
- 是否有模型 API key。
- demo 是否可用。
- 默认是否应该用 rule-only。

注意：绝对不要把 token 的值、前缀、长度、部分内容返回给前端。

### 4.4 修改 Try Demo 行为

当前 `fillDemo()` 只填输入框。

建议改为：

```text
Try Demo -> fetch("/api/demo-report") -> renderResults()
```

也就是点击后直接展示报告，而不是再让用户点 Analyze。

前端可以保留填充输入框的行为，但这只是辅助展示，不应该再依赖真实分析链路。

建议函数命名：

```js
async function loadDemoReport()
```

核心流程：

```text
setLoading(true)
hideError()
hideResults()
GET /api/demo-report
_report = data.report
_markdown = data.markdown
_duration = data.duration_seconds
renderResults()
showStatus("Demo report loaded", "ok")
setLoading(false)
```

### 4.5 真实分析默认 rule-only

当前页面默认 AI Review 是 On。

建议改成默认 Rule-only：

```html
<option value="0" selected>Rule-only</option>
<option value="1">AI Review</option>
```

原因：

- 开箱即用场景通常没有 `OPENAI_API_KEY`。
- 默认 AI On 容易让第一次使用的人误以为必须配置模型 Key。
- 后端虽然会 fallback，但 UI 语义不够清晰。

真实需要 AI 时，用户可以手动切换为 AI Review。

### 4.6 改善 GitHub API 失败提示

当前 GitHub API 失败时，后端返回：

```text
GitHub API error: ...
```

建议保留 502，但错误信息更面向用户：

```text
GitHub API request failed. For demo, click Try Demo. For real PR analysis, set GITHUB_TOKEN or use a public repository.
```

前端也可以继续做友好映射：

- rate limit -> 提示配置 `GITHUB_TOKEN` 或稍后再试。
- not found -> 检查 repo / PR 编号 / 私有仓库权限。
- authentication failed -> 检查 `.env` 里的 `GITHUB_TOKEN`。
- timeout -> 网络或 GitHub API 慢，建议重试。

## 5. 最小改动文件

建议只改这些文件：

```text
src/service/app.py
src/service/static/app.js
src/service/static/index.html
src/service/demo/openclaw_80419_full.json
docs/web-console-demo-first-plan.md
```

可选更新：

```text
README.md
```

README 只需要补充一段“一键演示”说明，不需要重写。

## 6. 执行顺序

### Step 1: 固化 demo 数据

从当前历史报告中选一份稳定文件：

```text
reports/history/openclaw_openclaw_80419_20260706-005906_full.json
```

复制到：

```text
src/service/demo/openclaw_80419_full.json
```

验收：

- 文件存在。
- JSON 可被 `json.load()` 解析。
- 顶层包含 `report`。
- 顶层包含 `markdown`。

### Step 2: 后端增加 demo/config 接口

在 `src/service/app.py` 增加：

```text
GET /api/config
GET /api/demo-report
```

验收：

```powershell
uv run uvicorn src.service.app:app --reload
```

打开：

```text
http://127.0.0.1:8000/api/config
http://127.0.0.1:8000/api/demo-report
```

应返回 JSON。

### Step 3: 前端 Try Demo 改为直接加载报告

修改 `src/service/static/app.js`：

- 新增 `loadDemoReport()`。
- `Try Demo` 按钮调用 `loadDemoReport()`。
- 成功后复用现有 `renderResults()`。

修改 `src/service/static/index.html`：

- 按钮 `onclick` 从 `fillDemo()` 改成 `loadDemoReport()`。

验收：

- 不填任何输入框，点击 Try Demo 可以展示报告。
- `.env` 为空也可以展示报告。
- 断开 GitHub token 也可以展示报告。

### Step 4: 默认关闭 AI Review

修改 `src/service/static/index.html`：

```html
<option value="0" selected>Rule-only</option>
<option value="1">AI Review</option>
```

验收：

- 打开页面时，AI Review 默认显示 Rule-only。
- 用户仍可手动选择 AI Review。

### Step 5: 改善真实分析错误提示

修改 `src/service/app.py` 的 GitHub API 异常返回文案。

验收：

- 输入不存在的 PR 时，页面能显示用户可理解的错误。
- GitHub rate limit 时，提示配置 `GITHUB_TOKEN` 或稍后再试。
- 错误提示不泄露 token 或内部堆栈。

## 7. 验收标准

### 7.1 开箱演示验收

在 `.env` 为空的情况下：

```powershell
cd C:\Users\HelicasE\Desktop\program\ai-pr-review\ai-pr-review
uv run uvicorn src.service.app:app --reload
```

打开：

```text
http://127.0.0.1:8000
```

点击：

```text
Try Demo
```

必须满足：

- 页面展示完整报告。
- Suggestions 可见。
- Completeness 可见。
- Report Markdown 可见。
- 不访问 GitHub API。
- 不访问 OpenAI / DeepSeek API。
- 不需要 `GITHUB_TOKEN`。
- 不需要 `OPENAI_API_KEY`。

### 7.2 真实 PR 分析验收

无 `OPENAI_API_KEY` 时：

- 可以 rule-only 分析。
- 不因为缺少模型 Key 崩溃。
- 报告中有 warning 或状态说明。

无 `GITHUB_TOKEN` 时：

- 公开仓库可以尝试匿名访问。
- 如果触发 rate limit，应给出清晰提示。

有 `GITHUB_TOKEN` 时：

- 可以稳定分析公开 PR。
- 如果 token 无效，应提示检查 `.env`。

### 7.3 回归测试

至少运行：

```powershell
uv run pytest tests/test_agent_policy.py tests/test_agent_trace.py tests/test_agent_runner_rule_only.py
```

如果时间允许，运行全量：

```powershell
uv run pytest
```

## 8. 对外演示话术

可以这样描述：

```text
这个项目分成 demo mode 和 real analysis mode。

Demo mode 完全离线，不依赖 GitHub token 和模型 API key，用于保证第一次打开就能看到完整审查体验。

Real analysis mode 会分析真实 GitHub PR。公开仓库可以匿名尝试，但为了稳定性推荐配置 GITHUB_TOKEN；如果配置 OPENAI_API_KEY，则启用 AI review，否则自动降级为 rule-only。
```

这个表达比“必须先配 token”更适合项目展示。

## 9. 后续可选增强

这些不是当前必做项：

- 本地化 `marked.min.js`，减少 CDN 依赖。
- 增加 `DEMO_MODE=true`，公开部署时禁用真实 GitHub 分析。
- 增加 `/api/health` 返回 config summary。
- 前端顶部显示环境状态条：

```text
Demo ready · GitHub token not configured · AI key not configured · Real PR uses rule-only/anonymous mode
```

- 增加第二份 demo report，用于展示高风险 PR。

## 10. 推荐结论

不要把“配置 token”作为 Web Console 能跑起来的前置条件。

正确路线是：

```text
Demo-first: 无配置也能展示完整能力
Real Analyze: 有配置时分析真实 PR，没配置时清晰降级
```

这样项目对外演示更稳定，也更符合“AI 是风险提示器，工程链路要能降级”的设计目标。
