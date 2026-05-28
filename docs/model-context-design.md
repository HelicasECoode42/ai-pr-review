# 模型选择与上下文策略

## 模型选择

MVP 推荐使用支持长上下文、代码理解和 JSON 输出稳定的通用模型，例如 OpenAI GPT-4.1 / GPT-4o / o 系列推理模型，或企业环境中的 Azure OpenAI 同等部署。

选择标准：

- 代码理解能力强：能解释跨函数影响和潜在行为变化
- JSON 输出稳定：便于本地校验和后处理
- 响应速度可控：PR Review 不应等待过久
- 成本可控：大 PR 需要分块分析
- 支持系统提示和严格输出格式

MVP 策略：

- 小 PR：单次模型调用完成总结和建议
- 中 PR：按文件或风险 hunk 分块，最后聚合总结
- 大 PR：只让模型分析高风险文件和规则命中片段，普通文件走启发式摘要

## 上下文获取方式

上下文分三层：

### 1. PR 级上下文

- 标题
- 描述
- 作者
- base/head branch
- 文件列表和 additions/deletions

用途：帮助模型判断 PR 意图，避免只看局部 diff 后误解变更目的。

### 2. Diff 级上下文

- unified diff hunk
- 新增/删除行
- GitHub new line 行号
- 文件状态：added/modified/removed/renamed

用途：生成可定位建议，限制模型只评论变更区域。

### 3. 项目级上下文

MVP 暂不做重型索引，只预留接口。后续可加入：

- 同文件未变更函数上下文
- 被调用函数定义
- 测试文件和业务文件映射
- README、架构文档、团队规范
- 历史 review comment 和 bug 记录

## Token 预算

上下文构建采用优先级裁剪：

1. 永远保留 PR 元信息
2. 永远保留文件列表和变更统计
3. 优先保留高风险文件和规则命中 hunk
4. 超预算时裁剪普通大文件 patch
5. 对超大文件只保留 hunk 摘要和规则证据

## Prompt 设计

Prompt 要求模型遵守：

- 只基于给定上下文判断
- 不确定时降低 confidence
- 不对未变更行给行级评论
- 每条建议必须包含证据
- 重点关注 bug、安全、性能、并发、数据一致性、测试缺口
- 避免风格、命名、格式等低价值评论，除非影响可维护性

## 输出结构

模型输出 JSON：

```json
{
  "summary": "string",
  "risk_level": "low|medium|high|critical",
  "suggestions": [
    {
      "file_path": "src/example.py",
      "line": 42,
      "severity": "medium",
      "confidence": 0.78,
      "title": "Missing authorization check",
      "reason": "The new endpoint reads user data without checking ownership.",
      "recommendation": "Validate the current user can access the target resource."
    }
  ]
}
```

本地代码会再次校验 severity、confidence、line 是否合法。
