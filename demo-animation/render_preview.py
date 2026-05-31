from __future__ import annotations

import math
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


W, H = 1920, 1080
FPS = 30
DURATION = 42

OUT_DIR = Path(__file__).resolve().parent / "media" / "preview"
FRAMES = OUT_DIR / "frames"
MP4 = OUT_DIR / "opening_flow_preview.mp4"
GIF = OUT_DIR / "opening_flow_preview.gif"
POSTER = OUT_DIR / "opening_flow_poster.png"

BG = "#090E1A"
GRID = "#111B2F"
PANEL = "#101827"
TEXT = "#EEF2F7"
MUTED = "#8B96A8"
BLUE = "#60A5FA"
CYAN = "#22D3EE"
GREEN = "#34D399"
AMBER = "#FBBF24"
PINK = "#F472B6"
RED = "#F87171"
WHITE = "#FFFFFF"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size, index=1 if bold and path.endswith(".ttc") else 0)
        except Exception:
            continue
    return ImageFont.load_default()


F_TITLE = font(82, True)
F_SUB = font(30)
F_H1 = font(54, True)
F_H2 = font(34, True)
F_BODY = font(24)
F_SMALL = font(18)
F_NODE = font(25, True)
F_NODE_BODY = font(16)
F_BADGE = font(22, True)
F_CLOSE = font(38, True)


def rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.strip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def rgba(hex_color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    r, g, b = rgb(hex_color)
    return r, g, b, alpha


def ease(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def reveal(t: float, start: float, duration: float) -> float:
    return ease((t - start) / duration)


def scene_alpha(t: float, start: float, end: float, fade: float = 0.9) -> float:
    return min(reveal(t, start, fade), 1 - reveal(t, end - fade, fade))


def text_bounds(text: str, fnt: ImageFont.ImageFont, spacing: int = 8) -> tuple[int, int, int, int]:
    probe = Image.new("RGBA", (4, 4))
    draw = ImageDraw.Draw(probe)
    return draw.multiline_textbbox((0, 0), text, font=fnt, spacing=spacing)


def write_text(
    image: Image.Image,
    xy: tuple[float, float],
    text: str,
    fnt: ImageFont.ImageFont,
    fill: str,
    progress: float,
    *,
    anchor: str = "mm",
    alpha: float = 1.0,
    spacing: int = 8,
    align: str = "center",
) -> None:
    progress = max(0.0, min(1.0, progress))
    alpha = max(0.0, min(1.0, alpha))
    if progress <= 0 or alpha <= 0:
        return

    bbox = text_bounds(text, fnt, spacing)
    tw = bbox[2] - bbox[0] + 22
    th = bbox[3] - bbox[1] + 22
    layer = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.multiline_text(
        (11 - bbox[0], 11 - bbox[1]),
        text,
        font=fnt,
        fill=rgba(fill, int(255 * alpha)),
        spacing=spacing,
        align=align,
    )

    mask = Image.new("L", (tw, th), 0)
    ImageDraw.Draw(mask).rectangle((0, 0, max(1, int(tw * progress)), th), fill=255)
    layer.putalpha(Image.composite(layer.getchannel("A"), Image.new("L", (tw, th), 0), mask))

    x, y = xy
    if anchor == "mm":
        pos = (int(x - tw / 2), int(y - th / 2))
    elif anchor == "lm":
        pos = (int(x), int(y - th / 2))
    else:
        pos = (int(x), int(y))
    image.alpha_composite(layer, pos)


def line(
    draw: ImageDraw.ImageDraw,
    p1: tuple[float, float],
    p2: tuple[float, float],
    color: str,
    progress: float,
    width: int = 5,
    alpha: float = 1.0,
) -> None:
    progress = max(0.0, min(1.0, progress))
    if progress <= 0:
        return
    x1, y1 = p1
    x2, y2 = p2
    draw.line(
        (x1, y1, x1 + (x2 - x1) * progress, y1 + (y2 - y1) * progress),
        fill=rgba(color, int(255 * alpha)),
        width=width,
    )


def arrow(
    draw: ImageDraw.ImageDraw,
    p1: tuple[float, float],
    p2: tuple[float, float],
    color: str,
    progress: float,
    width: int = 5,
    alpha: float = 1.0,
) -> None:
    line(draw, p1, p2, color, progress, width, alpha)
    if progress < 0.94:
        return
    x1, y1 = p1
    x2, y2 = p2
    xe = x1 + (x2 - x1) * progress
    ye = y1 + (y2 - y1) * progress
    angle = math.atan2(y2 - y1, x2 - x1)
    size = 18
    draw.polygon(
        [
            (xe, ye),
            (xe - size * math.cos(angle - 0.45), ye - size * math.sin(angle - 0.45)),
            (xe - size * math.cos(angle + 0.45), ye - size * math.sin(angle + 0.45)),
        ],
        fill=rgba(color, int(255 * alpha)),
    )


def rect_outline(
    draw: ImageDraw.ImageDraw,
    rect: tuple[float, float, float, float],
    color: str,
    progress: float,
    *,
    alpha: float = 1.0,
    width: int = 4,
    fill: bool = True,
) -> None:
    progress = max(0.0, min(1.0, progress))
    if progress <= 0:
        return
    x1, y1, x2, y2 = rect
    if fill:
        draw.rounded_rectangle(rect, radius=18, fill=rgba(PANEL, int(54 * alpha * progress)))
    pts = [(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)]
    lengths = [math.dist(pts[i], pts[i + 1]) for i in range(4)]
    remaining = sum(lengths) * progress
    for i, length in enumerate(lengths):
        if remaining <= 0:
            break
        p = min(1, remaining / length)
        line(draw, pts[i], pts[i + 1], color, p, width, alpha)
        remaining -= length


def card(
    image: Image.Image,
    cx: float,
    cy: float,
    w: float,
    h: float,
    title: str,
    body: str,
    color: str,
    progress: float,
    *,
    alpha: float = 1.0,
) -> None:
    draw = ImageDraw.Draw(image, "RGBA")
    rect_outline(draw, (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), color, reveal(progress, 0, 0.45), alpha=alpha)
    write_text(image, (cx, cy - h * 0.22), title, F_NODE, TEXT, reveal(progress, 0.32, 0.45), alpha=alpha, spacing=5)
    write_text(image, (cx, cy + h * 0.25), body, F_NODE_BODY, MUTED, reveal(progress, 0.52, 0.45), alpha=alpha, spacing=7)


def badge(image: Image.Image, x: float, y: float, text: str, color: str, progress: float, *, alpha: float = 1.0) -> None:
    if progress <= 0:
        return
    bbox = text_bounds(text, F_BADGE)
    tw = bbox[2] - bbox[0]
    draw = ImageDraw.Draw(image, "RGBA")
    line(draw, (x, y + 27), (x + tw + 44, y + 27), color, reveal(progress, 0, 0.55), 5, alpha)
    write_text(image, (x, y), text, F_BADGE, TEXT, reveal(progress, 0.22, 0.6), anchor="lm", alpha=alpha)


def paragraph(
    image: Image.Image,
    x: float,
    y: float,
    lines_: list[str],
    progress: float,
    *,
    alpha: float = 1.0,
    color: str = MUTED,
    fnt: ImageFont.ImageFont = F_BODY,
    gap: int = 44,
) -> None:
    for i, text in enumerate(lines_):
        write_text(image, (x, y + i * gap), text, fnt, color, reveal(progress, i * 0.16, 0.62), anchor="lm", alpha=alpha, align="left")


def base_frame(t: float) -> Image.Image:
    image = Image.new("RGBA", (W, H), rgba(BG, 255))
    draw = ImageDraw.Draw(image, "RGBA")
    drift = int((t * 7) % 120)
    for x in range(-120 + drift, W + 120, 120):
        draw.line((x, 0, x, H), fill=rgba(GRID, 118), width=1)
    for y in range(-120 + drift, H + 120, 120):
        draw.line((0, y, W, y), fill=rgba(GRID, 118), width=1)
    return image


def scene_title(image: Image.Image, t: float) -> None:
    alpha = scene_alpha(t, 0.0, 8.8, 0.8)
    write_text(image, (W / 2, 160), "AI PR Review Assistant", F_TITLE, TEXT, reveal(t, 0.3, 1.7), alpha=alpha)
    write_text(
        image,
        (W / 2, 235),
        "面向 GitHub Pull Request 生命周期的智能代码审查助手",
        F_SUB,
        MUTED,
        reveal(t, 1.5, 1.7),
        alpha=alpha,
    )
    write_text(image, (W / 2, 360), "比赛演示要证明什么？", F_H1, TEXT, reveal(t, 2.9, 1.5), alpha=alpha)

    badges = [
        ("能自动审查 PR", BLUE),
        ("不是简单 Prompt", GREEN),
        ("能回到代码现场", PINK),
    ]
    for i, (text, color) in enumerate(badges):
        badge(image, 450 + i * 365, 500, text, color, reveal(t, 4.5 + i * 0.45, 1.25), alpha=alpha)

    paragraph(
        image,
        430,
        665,
        [
            "所以开场先讲清楚：从一段 PR diff，如何变成结构化、可追溯、可落地的 Review 结果。",
            "后续实录再展示 Web Console、GitHub Actions 和 VS Code 插件三条入口。",
        ],
        reveal(t, 6.0, 1.8),
        alpha=alpha,
        color=MUTED,
        fnt=F_BODY,
        gap=48,
    )


def scene_problem(image: Image.Image, t: float) -> None:
    alpha = scene_alpha(t, 8.2, 17.0, 0.9)
    if alpha <= 0:
        return
    write_text(image, (W / 2, 145), "痛点：大型 PR 的 Review 成本很高", F_H1, TEXT, reveal(t, 8.6, 1.5), alpha=alpha)
    paragraph(
        image,
        250,
        250,
        [
            "人工审查不是逐行扫 diff 那么简单。",
            "审查者要先理解变更意图，再定位风险，还要把结论带回代码上下文。",
        ],
        reveal(t, 10.0, 1.8),
        alpha=alpha,
        fnt=F_BODY,
        gap=48,
    )

    problems = [
        (360, 520, "文件多", "diff 长，先建立全局理解", RED),
        (760, 520, "风险散", "鉴权、SQL、日志、CI 变更", AMBER),
        (1160, 520, "上下文缺", "只看 patch 容易误判", PINK),
        (1560, 520, "来回切", "GitHub 和 IDE 反复跳转", BLUE),
    ]
    for i, (cx, cy, title, body, color) in enumerate(problems):
        card(image, cx, cy, 300, 138, title, body, color, reveal(t, 11.9 + i * 0.45, 1.35), alpha=alpha)

    write_text(
        image,
        (W / 2, 790),
        "目标不是替代人工 Review，而是把重复的上下文整理、风险预筛和代码定位自动化。",
        F_H2,
        TEXT,
        reveal(t, 14.4, 1.7),
        alpha=alpha,
    )


PIPE = [
    (160, "GitHub PR", "元信息\nchanged files\npatch", BLUE),
    (430, "Diff Parser", "hunk 解析\n新增行映射", CYAN),
    (700, "Risk Rules", "路径风险\n代码模式\nAST 规则", AMBER),
    (970, "Context Pack", "Review Guide\n函数索引\n架构约定", PINK),
    (1240, "LLM Reviewer", "结构化 JSON\n原因与建议", BLUE),
    (1510, "Filter", "置信度\n变更行\n去重限流", GREEN),
    (1780, "ReviewReport", "Markdown\nJSON\n完整性", GREEN),
]


def scene_engine(image: Image.Image, t: float) -> None:
    alpha = scene_alpha(t, 16.4, 29.0, 0.9)
    if alpha <= 0:
        return
    write_text(image, (W / 2, 132), "核心：不是让模型自由发挥，而是一条工程化 Review Pipeline", F_H1, TEXT, reveal(t, 16.9, 1.8), alpha=alpha)
    paragraph(
        image,
        160,
        225,
        [
            "系统先用 GitHub API 和本地规则缩小问题空间，再把证据组织成受控上下文。",
            "模型输出必须符合 schema，最终建议还要经过本地过滤，才进入报告和评论。",
        ],
        reveal(t, 18.5, 1.9),
        alpha=alpha,
        color=MUTED,
        fnt=F_BODY,
        gap=44,
    )

    y = 545
    draw = ImageDraw.Draw(image, "RGBA")
    for i, (cx, title, body, color) in enumerate(PIPE):
        card(image, cx, y, 220, 132, title, body, color, reveal(t, 20.8 + i * 0.36, 1.2), alpha=alpha)
        if i < len(PIPE) - 1:
            arrow(draw, (cx + 112, y), (PIPE[i + 1][0] - 112, y), color, reveal(t, 22.8 + i * 0.28, 1.0), 4, alpha)

    # Soft moving proof dots.
    for i in range(len(PIPE) - 1):
        x1 = PIPE[i][0] + 112
        x2 = PIPE[i + 1][0] - 112
        q = (t - 25.0 - i * 0.18) / 2.8
        if 0 <= q <= 1:
            x = x1 + (x2 - x1) * q
            draw.ellipse((x - 8, y - 8, x + 8, y + 8), fill=rgba(WHITE, int(255 * alpha)), outline=rgba(PIPE[i][3], int(255 * alpha)), width=3)

    write_text(image, (W / 2, 800), "关键价值：可读、可信、可追溯，而不是一段不可控的 AI 文本。", F_H2, TEXT, reveal(t, 26.0, 1.6), alpha=alpha)


def scene_reliability(image: Image.Image, t: float) -> None:
    alpha = scene_alpha(t, 28.2, 35.3, 0.8)
    if alpha <= 0:
        return
    write_text(image, (W / 2, 145), "可靠性：坏 PR 也要能产出有用报告", F_H1, TEXT, reveal(t, 28.6, 1.4), alpha=alpha)

    left_x = 350
    right_x = 1180
    y0 = 330
    draw = ImageDraw.Draw(image, "RGBA")
    write_text(image, (left_x, y0), "稳定 reviewer", F_H2, BLUE, reveal(t, 30.0, 1.0), alpha=alpha)
    paragraph(
        image,
        left_x - 140,
        y0 + 85,
        [
            "workflow 从 base 分支运行审查器，",
            "即使 PR 改坏 reviewer 自身，",
            "也尽量生成诊断报告。",
        ],
        reveal(t, 30.8, 1.5),
        alpha=alpha,
        fnt=F_BODY,
        gap=42,
    )

    write_text(image, (right_x, y0), "PR head 诊断", F_H2, AMBER, reveal(t, 31.0, 1.0), alpha=alpha)
    paragraph(
        image,
        right_x - 170,
        y0 + 85,
        [
            "PR 分支单独 compileall，",
            "语法错误会写入报告，",
            "最后一步再让 workflow 失败。",
        ],
        reveal(t, 31.8, 1.5),
        alpha=alpha,
        fnt=F_BODY,
        gap=42,
    )

    arrow(draw, (720, 545), (1020, 545), GREEN, reveal(t, 32.8, 1.1), 6, alpha)
    write_text(
        image,
        (W / 2, 720),
        "报告生成成功 ≠ PR 可以合并",
        F_H2,
        TEXT,
        reveal(t, 33.4, 1.1),
        alpha=alpha,
    )
    write_text(
        image,
        (W / 2, 790),
        "诊断和合并门禁分离，才能避免“工具挂了就没有结论”。",
        F_BODY,
        MUTED,
        reveal(t, 34.0, 1.1),
        alpha=alpha,
    )


def scene_delivery(image: Image.Image, t: float) -> None:
    alpha = reveal(t, 34.7, 0.9)
    if alpha <= 0:
        return
    write_text(image, (W / 2, 130), "交付闭环：同一份 ReviewReport，进入三个真实使用场景", F_H1, TEXT, reveal(t, 35.0, 1.5), alpha=alpha)

    report_progress = reveal(t, 36.2, 1.2)
    card(image, W / 2, 350, 420, 150, "ReviewReport", "风险等级 / 代码位置\n原因 / 建议 / 完整性\nReviewMeta / JSON", GREEN, report_progress, alpha=alpha)

    outputs = [
        (390, 650, "GitHub Actions", "自动审查 PR\nsummary comment\n可选 inline comment", BLUE),
        (960, 650, "Web Console", "粘贴 PR URL\n可视化建议\n下载 JSON", GREEN),
        (1530, 650, "VS Code 插件", "Problems\nReview Panel\nOpen Code", PINK),
    ]
    draw = ImageDraw.Draw(image, "RGBA")
    for i, (cx, cy, title, body, color) in enumerate(outputs):
        arrow(draw, (W / 2, 430), (cx, cy - 95), color, reveal(t, 37.0 + i * 0.25, 1.0), 4, alpha)
        card(image, cx, cy, 360, 170, title, body, color, reveal(t, 37.5 + i * 0.35, 1.2), alpha=alpha)

    write_text(
        image,
        (W / 2, 900),
        "从 PR diff 到代码现场：让审查结果真正被开发者使用。",
        F_CLOSE,
        TEXT,
        reveal(t, 40.0, 1.3),
        alpha=alpha,
    )


def render_frame(t: float) -> Image.Image:
    image = base_frame(t)
    scene_title(image, t)
    scene_problem(image, t)
    scene_engine(image, t)
    scene_reliability(image, t)
    scene_delivery(image, t)
    return image.convert("RGB")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    FRAMES.mkdir(parents=True, exist_ok=True)
    for old in FRAMES.glob("frame_*.png"):
        old.unlink()

    for i in range(FPS * DURATION):
        render_frame(i / FPS).save(FRAMES / f"frame_{i:04d}.png")
    render_frame(38.5).save(POSTER)

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-framerate",
            str(FPS),
            "-i",
            str(FRAMES / "frame_%04d.png"),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(MP4),
        ],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(MP4),
            "-vf",
            "fps=12,scale=960:-1:flags=lanczos",
            str(GIF),
        ],
        check=True,
    )
    print(MP4)
    print(GIF)
    print(POSTER)


if __name__ == "__main__":
    main()
