# Web 报告输出安全

`/api/analyze/stream`、demo 和历史报告都经 `renderResults()` 汇入 `renderMarkdown()`。报告 Markdown、GitHub 元数据和历史记录均视为不可信内容。

前端固定提供本地 Marked 18.1.1。渲染器把原始 HTML token 转义为文字，图片 token 只显示转义后的 alt 文本，链接 label/title 逐项转义；链接地址经过 `URL` 解析，只允许 `http:`、`https:` 和 `mailto:`。Marked 不可用或解析失败时，报告通过 `textContent` 显示原文。PR 和历史 GitHub 链接仅接受 HTTP(S)，代码文件路径按 URL 路径段编码。普通标题、列表、表格、换行和代码块仍按 Markdown 格式展示。

离线验收运行 `python -m pytest tests/test_web_output_security.py -q`。测试执行真实前端 `renderMarkdown()` 与本地 Marked，并用 Python 标准库 HTMLParser 检查解析后的标签、属性和链接协议；攻击样例不会执行，也不会访问网络。仓库没有浏览器 DOM 测试运行时，因此这组离线检查不等同于真实浏览器中的端到端 XSS 证明。若需要更强的 DOM 级验证，应在现有测试环境提供可信 DOM 实现后增加验证。
