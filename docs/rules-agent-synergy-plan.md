# Rules & Agent 协同改进方案

> 当前核心问题：规则太重、Agent 太轻，两者各说各话。本文档描述如何将规则降级为信号层，Agent 升级为决策层。

---

## 一、现状诊断

### 1.1 规则层的问题

当前 `src/analyzer/risk_rules.py` 约 15 条规则，分为路径规则和代码模式规则：

| 类别 | 规则 | 问题 |
|---|---|---|
| 路径规则 | `auth\|permission\|rbac\|session\|jwt\|token` → medium | 粒度太粗。改一行 `print(token)` 调试代码和改 token 签发逻辑触发同一条规则 |
| 路径规则 | `config\|settings\|secret\|credential` → high | 过于泛化。"改一个 config 变量名"和"泄露 secret key"不是同等级风险 |
| 路径规则 | `.github/workflows/` → high | 所有 workflow 变更一视同仁，不改 Yaml 注释和改门禁逻辑都是 HIGH |
| 代码规则 | `eval/exec` → critical | 确定性高，但同样需要 AI 判断上下文（是用户输入eval还是固定字符串eval） |
| 代码规则 | `subprocess/os.system` → critical | 同上 |
| 代码规则 | SQL 字符串拼接 → high | 同上 |
| 代码规则 | 吞异常 `except: pass` → medium | 误报率极高。ORM 的 `session.rollback() except: pass` 是常规写法 |
| 代码规则 | 测试跳过 `@pytest.mark.skip` → low | 临时跳过和永久跳过无法区分 |
| 代码规则 | 测试断言删除 → medium | 重构测试时大量删除断言是正常的 |

**根因**：规则的正则是粗粒度的，没有 AI 做二次验证。规则命中直接进 `report.suggestions`，和 AI 的建议平行展示。

### 1.2 Agent 层的问题

当前 `ReviewAgentRunner` 只是"顺序调用工具 + 记录 trace"：

```
fetch_pr → scan_risks → build_context → one_shot_ai → filter → render
```

- `choose_agent_strategy` 只根据文件数量和行数做策略决策，不根据规则命中内容做决策
- Agent 没有"验证规则信号"的 tool
- AI 在 prompt 里看到了 `findings` 列表，但没有"确认或推翻"的职责

### 1.3 核心矛盾

```
规则输出 ──→ report.suggestions（直接给用户看）
AI 输出  ──→ report.suggestions（直接给用户看）

两者是平行关系，不互验。
同一个 src/auth.py 变更：
  - 规则："auth code changed — MEDIUM"
  - AI："LGTM，只是 import 调整"
  → 两条平行的建议出现在报告里，审查者要自己判断谁对
```

---

## 二、改进方案

### 核心思路：规则降级为信号层，Agent 升级为决策层

```
旧流程：
  scan_risks → findings → 直接进 report
  AI review  → suggestions → 直接进 report

新流程：
  scan_risks → signals（不直接输出给用户）
    │
    ▼
  Agent 拿到 signals + diff context
    ├─ verify_signal(signal_1): AI 看 diff → 确认 / 驳回 / 调整严重度
    ├─ verify_signal(signal_2): AI 看 diff → ...
    └─ verify_signal(signal_n): AI 看 diff → ...
    │
    ▼
  只有 AI 确认过的 signals → ReviewSuggestion → report
  AI 驳回的 signals    → dismissed_signals（留给审查者参考，但不刷屏）
```

### 2.1 新增数据类型

```python
# src/models.py — 新增

class RuleSignal(BaseModel):
    """规则扫描的原始信号，需经 Agent 验证后才输出为建议。"""
    file_path: str
    line: int | None = None
    severity: Severity
    rule_id: str
    title: str
    evidence: str
    confidence: float = 0.5          # 规则的初始置信度（比 RiskFinding 低）
    source: str = "rule"             # "rule" | "ast"


class SignalVerification(BaseModel):
    """Agent 对单条 RuleSignal 的验证结果。"""
    signal: RuleSignal
    verified: bool                   # AI 确认该信号属实
    adjusted_severity: Severity | None = None  # AI 调整后的严重度
    reason: str                       # AI 的判断理由
    model_confidence: float = 0.0    # AI 对自己判断的置信度
```

### 2.2 新增 Agent Tool: `verify_signals`

```python
# src/agent/runner.py — 新增 tool

def _verify_signals(ctx: dict) -> None:
    """对每条 rule signal 做 AI 验证，拆分出 confirmed 和 dismissed。"""
    signals: list[RuleSignal] = ctx.get("signals", [])

    if not signals:
        ctx["confirmed_signals"] = []
        ctx["dismissed_signals"] = []
        return

    confirmed: list[RuleSignal] = []
    dismissed: list[RuleSignal] = []

    for signal in signals:
        # 只对 MEDIUM 及以上的信号做 AI 验证
        # LOW 信号直接丢弃（规则误报率太高）
        if signal.severity == Severity.LOW:
            dismissed.append(signal)
            continue

        # CRITICAL 信号即使 AI 不确认也要保留（安全阻断类）
        if signal.severity == Severity.CRITICAL:
            confirmed.append(signal)
            continue

        # 其余信号：用 AI 验证
        result = _verify_one_signal(signal, ctx)
        if result.verified:
            if result.adjusted_severity:
                signal.severity = result.adjusted_severity
            confirmed.append(signal)
        else:
            dismissed.append(signal)

    ctx["confirmed_signals"] = confirmed
    ctx["dismissed_signals"] = dismissed
```

验证单个信号的 prompt 设计：

```
System: You are a code reviewer verifying a static analysis finding.

Input:
- Rule finding: {file_path}:{line} — {title} — {evidence}
- Diff context: {patch_hunk}

Task: Determine if this rule finding is a real issue.
- If the diff shows the rule correctly identified a real risk → confirmed: true
- If the diff shows a false positive (e.g. comment change, debugging code,
  refactoring, standard pattern) → confirmed: false
- You may adjust the severity upward or downward based on the actual risk

Return JSON:
{
  "verified": true/false,
  "adjusted_severity": "low|medium|high|critical" or null,
  "reason": "brief explanation of your judgment",
  "confidence": 0.0-1.0
}
```

### 2.3 Agent Runner 新流程

```python
# Runner 中 rule_only 路径的变化

# 旧：
#   scan_risks → build_rule_only_report

# 新：
#   scan_risks（输出 signals，不是 findings）
#   → verify_signals（AI 验证，无 AI 时跳过验证，全部 signals 视为 confirmed）
#   → build_rule_only_report（输入 confirmed_signals）

# one_shot_ai 路径的变化：
#   scan_risks → verify_signals → one_shot_ai_review
#   （AI review 的 prompt 里传入 confirmed_signals 和 dismissed_signals）
```

### 2.4 规则命中影响审查策略

```python
# src/agent/policy.py — 增强 choose_agent_strategy

def choose_agent_strategy(
    *,
    use_ai: bool,
    has_api_key: bool,
    files_count: int,
    additions: int,
    findings_count: int,
    high_severity_count: int,
    # ★ 新增参数
    signal_files: set[str] | None = None,   # 规则信号涉及的文件集合
    critical_signal_files: set[str] | None = None,  # CRITICAL 信号涉及的文件
    requested_mode: str = "auto",
) -> tuple[str, str]:
    # ... AI 不可用检查不变 ...

    # ── 显式指定优先 ──
    if requested_mode != "auto":
        ...

    # ── 自动选择 ──

    # CRITICAL 信号集中在少数文件 → 跳过 triage，直接 deep_review
    if critical_signal_files and len(critical_signal_files) <= 3 and files_count > 10:
        return ("two_stage", "CRITICAL signals concentrated in ≤3 files; deep-dive them")

    # 规则信号分散在大量文件中 → 需要 triage 定位热点
    if signal_files and len(signal_files) >= 8:
        return ("two_stage", "Rule signals spread across ≥8 files; triage needed")

    # 规则信号为空但 PR 很大 → 仍然需要 AI（可能是设计变更，非代码模式风险）
    if not signal_files and files_count > 15:
        return ("one_shot_ai", "Large PR with no rule signals; AI review for design risks")

    return ("one_shot_ai", "Default strategy")
```

---

## 三、规则精简方案

当前 ~15 条规则精简为 ~8 条核心规则，分为三类：

### 3.1 安全阻断类（保留，CRITICAL）

| 规则 | 正则 | 说明 |
|---|---|---|
| `eval-exec` | `\beval\s*\(` `\bexec\s*\(` | 动态代码执行 |
| `shell-command` | `subprocess\.(call\|run\|Popen)\s*\(` `os\.system\s*\(` | Shell 命令执行 |
| `sql-concat` | `[\"']\s*(?:SELECT\|INSERT\|UPDATE\|DELETE).*\+` `f[\"'].*(?:SELECT\|INSERT).*` | SQL 字符串拼接 |

### 3.2 路径信号类（保留，MEDIUM-HIGH）

| 规则 | 正则 | 说明 |
|---|---|---|
| `risk-path-auth` | `auth\|permission\|rbac\|session\|jwt\|token` | 鉴权相关代码变更 |
| `risk-path-payment` | `payment\|billing\|invoice\|checkout` | 支付相关 |
| `risk-path-workflow` | `.github/workflows/` | CI/CD 流水线 |
| `risk-path-migration` | `migration\|schema\|alter` | 数据库 schema 变更 |

> 删除 `config\|settings\|secret\|credential` — 太泛，AI 更适合判断。

### 3.3 质量提示类（保留，MEDIUM）

| 规则 | 正则 | 说明 |
|---|---|---|
| `swallowed-exception` | `except\s+.*:\s*pass` `except\s+.*:\s*continue` | 吞异常 |
| `sensitive-log` | `print\s*\(\s*.*(?:token\|password\|secret\|key)` `log.*(?:token\|password)` | 敏感信息日志 |

> 删除 `test-skip` 和 `test-assertion-removal` — 误报率太高，AI 更适合判断测试质量。

---

## 四、对现有模块的影响

| 文件 | 改动 | 说明 |
|---|---|---|
| `src/models.py` | 新增 `RuleSignal`、`SignalVerification`；`ReviewReport` 新增 `dismissed_signals` 字段 | 向后兼容：新字段默认空列表 |
| `src/analyzer/risk_rules.py` | `scan_risks` 返回 `list[RuleSignal]` 替代 `list[RiskFinding]`；精简规则 | `RiskFinding` 保留为兼容别名 |
| `src/reviewer/engine.py` | `build_rule_only_report` 接收 `signals` 替代 `findings` | 接口微调 |
| `src/reviewer/prompt.py` | `build_user_prompt` 增加 `dismissed_signals` 上下文 | AI 知道哪些规则被驳回了 |
| `src/agent/runner.py` | 新增 `verify_signals` tool；`_register_tools` 调整 tool 顺序 | 增量 |
| `src/agent/policy.py` | `choose_agent_strategy` 增加 signal 相关参数 | 增量 |
| `src/output/markdown.py` | 报告增加 "已驳回信号" 折叠区 | 不影响现有格式 |
| `tests/` | 更新 risk_rules、engine、runner 测试 | — |

---

## 五、执行顺序

| 步骤 | 内容 | 依赖 | 可独立合入 |
|---|---|---|---|
| 1 | `RuleSignal` 和 `SignalVerification` 数据模型 | 无 | ✅ |
| 2 | `scan_risks` 改为返回 `RuleSignal`，精简规则 | 步骤 1 | ✅ |
| 3 | `verify_signals` Agent tool | 步骤 2 | ✅ |
| 4 | `choose_agent_strategy` 接收 signal 做决策 | 步骤 2 | ✅ |
| 5 | `build_review_report` 区分 confirmed / dismissed | 步骤 3 | ✅ |
| 6 | Markdown 报告增加 "已驳回信号" 展示 | 步骤 5 | ✅ |
| 7 | AI prompt 注入 dismissed signals 上下文 | 步骤 5 | ✅ |

---

## 六、验收标准

- [ ] `uv run pytest` 全部通过
- [ ] CRITICAL 信号（eval/exec/shell）不经过 AI 验证直接进入报告
- [ ] MEDIUM 信号经过 AI 验证，确认后才进入报告
- [ ] LOW 信号直接丢弃
- [ ] 无 AI 时（`--no-ai`），所有信号视为 confirmed，行为与旧 `rule_only` 一致
- [ ] AI 验证失败的信号（provider timeout 等）→ 视为 confirmed（宁可多报不漏报）
- [ ] `dismissed_signals` 在 JSON report 中可见
- [ ] Markdown 报告不影响现有格式
- [ ] Web Console 和 VS Code 插件不受影响
