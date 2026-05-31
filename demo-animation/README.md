# Demo Opening Animation

Manim animation assets for the opening flow diagram in the AI PR Review demo video.

## Render

Install Manim in your preferred environment, then run from the project root:

```bash
manim -pqh demo-animation/opening_flow.py OpeningFlow
```

For a faster Manim preview:

```bash
manim -pql demo-animation/opening_flow.py OpeningFlow
```

Manim renders files into `demo-animation/media/`, which is ignored by git.

If Manim's native Cairo dependencies are not available locally, generate a
Pillow/ffmpeg preview instead:

```bash
python demo-animation/render_preview.py
```

## Scene

`OpeningFlow` introduces the product flow:

```text
大型 PR 审查痛点
  -> GitHub PR Diff
  -> GitHub API
  -> Diff Parser
  -> Risk Rules + Context Pack
  -> LLM Reviewer
  -> Structured ReviewReport
  -> GitHub / Web Console / VS Code
```

The later live product demo clips can be edited after this opening animation.
