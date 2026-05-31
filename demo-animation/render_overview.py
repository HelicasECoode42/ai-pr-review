from __future__ import annotations

import math
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


W, H = 1920, 1080
FPS = 24
DURATION = 65

OUT_DIR = Path(__file__).resolve().parent / "media" / "overview"
FRAMES = OUT_DIR / "frames"
MP4 = OUT_DIR / "overview.mp4"
GIF = OUT_DIR / "overview.gif"
POSTER = OUT_DIR / "poster.png"

BG = "#1a1a2e"
GRID = "#262946"
PANEL = "#20223a"
PANEL_DARK = "#15172a"
TEXT = "#ffffff"
MUTED = "#a8adbd"
MUTED_SOFT = "#7f8598"
CYAN = "#4ecdc4"
ORANGE = "#ff6b35"
PURPLE = "#9b59b6"
GOLD = "#f7dc6f"
GREEN = "#2ecc71"
RED = "#e74c3c"
BLUE = "#3498db"
AMBER = "#f39c12"


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


F_TITLE = font(60, True)
F_H1 = font(48, True)
F_H2 = font(34, True)
F_BODY = font(26)
F_BODY_BOLD = font(27, True)
F_SMALL = font(20)
F_TINY = font(17)
F_NUM = font(78, True)
F_CARD = font(30, True)
F_CARD_BODY = font(24)
F_CLOSE = font(40, True)


def rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.strip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def rgba(hex_color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    r, g, b = rgb(hex_color)
    return r, g, b, alpha


def clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def ease(x: float) -> float:
    x = clamp(x)
    return x * x * (3 - 2 * x)


def ease_out(x: float) -> float:
    x = clamp(x)
    return 1 - (1 - x) ** 3


def reveal(t: float, start: float, duration: float) -> float:
    return ease((t - start) / duration)


def scene_alpha(t: float, start: float, end: float, fade: float = 0.85) -> float:
    return min(reveal(t, start, fade), 1 - reveal(t, end, fade))


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
    progress = clamp(progress)
    alpha = clamp(alpha)
    if progress <= 0 or alpha <= 0:
        return

    bbox = text_bounds(text, fnt, spacing)
    tw = bbox[2] - bbox[0] + 28
    th = bbox[3] - bbox[1] + 28
    layer = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.multiline_text(
        (14 - bbox[0], 14 - bbox[1]),
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
    elif anchor == "rm":
        pos = (int(x - tw), int(y - th / 2))
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
    progress = clamp(progress)
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
    if progress < 0.97:
        return
    x1, y1 = p1
    x2, y2 = p2
    xe = x1 + (x2 - x1) * progress
    ye = y1 + (y2 - y1) * progress
    angle = math.atan2(y2 - y1, x2 - x1)
    size = 17
    draw.polygon(
        [
            (xe, ye),
            (xe - size * math.cos(angle - 0.48), ye - size * math.sin(angle - 0.48)),
            (xe - size * math.cos(angle + 0.48), ye - size * math.sin(angle + 0.48)),
        ],
        fill=rgba(color, int(255 * alpha)),
    )


def rounded_panel(
    draw: ImageDraw.ImageDraw,
    rect: tuple[float, float, float, float],
    color: str,
    progress: float,
    *,
    alpha: float = 1.0,
    fill_color: str = PANEL_DARK,
    fill_alpha: int = 120,
    width: int = 3,
    radius: int = 16,
) -> None:
    progress = clamp(progress)
    if progress <= 0:
        return
    x1, y1, x2, y2 = rect
    draw.rounded_rectangle(rect, radius=radius, fill=rgba(fill_color, int(fill_alpha * alpha * progress)))
    pts = [(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)]
    lengths = [math.dist(pts[i], pts[i + 1]) for i in range(4)]
    remaining = sum(lengths) * progress
    for i, length_ in enumerate(lengths):
        if remaining <= 0:
            break
        p = min(1.0, remaining / length_)
        line(draw, pts[i], pts[i + 1], color, p, width, alpha)
        remaining -= length_


def pill(
    image: Image.Image,
    cx: float,
    cy: float,
    text: str,
    color: str,
    progress: float,
    *,
    alpha: float = 1.0,
    fnt: ImageFont.ImageFont = F_SMALL,
) -> None:
    if progress <= 0:
        return
    bbox = text_bounds(text, fnt)
    w = bbox[2] - bbox[0] + 44
    h = bbox[3] - bbox[1] + 24
    draw = ImageDraw.Draw(image, "RGBA")
    rounded_panel(
        draw,
        (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2),
        color,
        reveal(progress, 0, 0.5),
        alpha=alpha,
        fill_color=PANEL,
        fill_alpha=125,
        width=2,
        radius=18,
    )
    write_text(image, (cx, cy), text, fnt, TEXT, reveal(progress, 0.25, 0.55), alpha=alpha)


def base_frame(t: float) -> Image.Image:
    image = Image.new("RGBA", (W, H), rgba(BG, 255))
    draw = ImageDraw.Draw(image, "RGBA")
    drift = int((t * 8) % 120)
    for x in range(-120 + drift, W + 120, 120):
        draw.line((x, 0, x, H), fill=rgba(GRID, 82), width=1)
    for y in range(-120 + drift, H + 120, 120):
        draw.line((0, y, W, y), fill=rgba(GRID, 82), width=1)
    return image


def scene_one(image: Image.Image, t: float) -> None:
    alpha = scene_alpha(t, 0.0, 10.0)
    if alpha <= 0:
        return
    draw = ImageDraw.Draw(image, "RGBA")
    write_text(image, (120, 95), "问题规模：一个中型 PR，已经超过人工快速扫描范围", F_H1, TEXT, reveal(t, 0.25, 1.3), anchor="lm", alpha=alpha)

    rounded_panel(draw, (130, 185, 1060, 850), CYAN, reveal(t, 1.0, 1.1), alpha=alpha)
    write_text(image, (175, 240), "PR #42  引入自动审查与编辑器闭环", F_H2, TEXT, reveal(t, 1.45, 1.1), anchor="lm", alpha=alpha)

    files = [
        ("src/reviewer/engine.py", "+102 / -19"),
        ("src/analyzer/context_builder.py", "+44 / -12"),
        ("src/service/app.py", "+96 / -8"),
        ("vscode-extension/src/panel.ts", "+118 / -31"),
        (".github/workflows/ai-pr-review.yml", "+64 / -10"),
        ("src/github/client.py", "+58 / -14"),
        ("...还有 17 个文件", ""),
    ]
    y0 = 328
    for i, (path, delta) in enumerate(files):
        p = reveal(t, 2.0 + i * 0.38, 0.9)
        y = y0 + i * 66
        if p <= 0:
            continue
        draw.rounded_rectangle((170, y - 27, 1020, y + 28), radius=8, fill=rgba(PANEL, int(86 * alpha * p)))
        write_text(image, (205, y), path, F_BODY, TEXT if i < 6 else MUTED, p, anchor="lm", alpha=alpha, align="left")
        if delta:
            write_text(image, (980, y), delta, F_BODY_BOLD, GREEN if i % 2 == 0 else GOLD, p, anchor="rm", alpha=alpha)

    rounded_panel(draw, (1190, 235, 1715, 640), GOLD, reveal(t, 3.1, 1.2), alpha=alpha, fill_alpha=95)
    counter_p = reveal(t, 3.8, 2.8)
    count = int(1000 * ease_out(counter_p))
    label = f"总变更：{count if count < 1000 else '1000+'} 行"
    write_text(image, (1455, 340), label, F_TITLE, GOLD, counter_p, alpha=alpha)
    write_text(image, (1248, 455), "涉及模块", F_H2, TEXT, reveal(t, 4.9, 1.1), anchor="lm", alpha=alpha)
    write_text(
        image,
        (1248, 535),
        "分析器 · 审查引擎\nWeb 服务 · 编辑器插件\n持续集成",
        F_BODY,
        MUTED,
        reveal(t, 5.45, 1.25),
        anchor="lm",
        alpha=alpha,
        spacing=14,
        align="left",
    )

    scan_p = reveal(t, 6.9, 2.0)
    x = 205 + 600 * (0.5 + 0.5 * math.sin(t * 4.0))
    if scan_p > 0:
        draw.ellipse((x - 32, 430 - 32, x + 32, 430 + 32), outline=rgba(BLUE, int(230 * alpha * scan_p)), width=5)
        draw.line((x + 25, 455, x + 72, 502), fill=rgba(BLUE, int(230 * alpha * scan_p)), width=6)
    write_text(image, (1455, 770), "人会漏。", F_NUM, RED, reveal(t, 8.0, 1.2), alpha=alpha)


def scene_two(image: Image.Image, t: float) -> None:
    alpha = scene_alpha(t, 10.0, 20.0)
    if alpha <= 0:
        return
    draw = ImageDraw.Draw(image, "RGBA")
    write_text(image, (W / 2, 110), "直觉方案为什么不够", F_H1, TEXT, reveal(t, 10.25, 1.1), alpha=alpha)

    boxes = [
        (430, "差异 + 提示词", CYAN),
        (960, "大模型", PURPLE),
        (1490, "报告", GOLD),
    ]
    for i, (cx, title, color) in enumerate(boxes):
        rounded_panel(draw, (cx - 185, 270, cx + 185, 390), color, reveal(t, 10.9 + i * 0.28, 0.9), alpha=alpha)
        write_text(image, (cx, 330), title, F_H2, TEXT, reveal(t, 11.2 + i * 0.28, 0.8), alpha=alpha)
        if i < len(boxes) - 1:
            arrow(draw, (cx + 205, 330), (boxes[i + 1][0] - 205, 330), color, reveal(t, 11.35 + i * 0.35, 0.9), 5, alpha)

    problems = [
        (520, "× 输入噪声", "包锁文件 · 演示报告\n二进制 · 生成产物", RED),
        (1400, "× 输出不可控", "未变更行 · 重复建议\n低置信刷屏", RED),
    ]
    for i, (cx, title, body, color) in enumerate(problems):
        p = reveal(t, 13.0 + i * 0.55, 1.0)
        rounded_panel(draw, (cx - 315, 560, cx + 315, 785), color, p, alpha=alpha, fill_alpha=105)
        write_text(image, (cx, 625), title, F_H2, color, reveal(t, 13.25 + i * 0.55, 0.9), alpha=alpha)
        write_text(image, (cx, 715), body, F_BODY, TEXT, reveal(t, 13.85 + i * 0.55, 1.0), alpha=alpha, spacing=13)

    write_text(image, (W / 2, 910), "裸调不够。", F_CLOSE, TEXT, reveal(t, 17.0, 1.25), alpha=alpha)


PIPE = [
    ("①", "差异解析器", "变更行映射", CYAN),
    ("②", "规则预扫描", "路径 · 正则 · AST", ORANGE),
    ("③", "上下文构建", "跳过噪声\n注入审查约定\n函数索引", BLUE),
    ("④", "模型调用", "结构化输出", PURPLE),
    ("⑤", "过滤层", "变更行校验\n置信度 · 去重\n数量上限", GREEN),
    ("⑥", "统一报告", "全链路\n同一结构", GOLD),
]


def pipeline_node(
    image: Image.Image,
    cx: float,
    cy: float,
    num: str,
    title: str,
    body: str,
    color: str,
    progress: float,
    alpha: float,
) -> None:
    draw = ImageDraw.Draw(image, "RGBA")
    rounded_panel(draw, (cx - 132, cy - 82, cx + 132, cy + 82), color, reveal(progress, 0, 0.55), alpha=alpha, fill_alpha=118, radius=14)
    write_text(image, (cx - 94, cy - 42), num, F_H2, color, reveal(progress, 0.18, 0.55), alpha=alpha)
    write_text(image, (cx, cy - 35), title, F_SMALL, TEXT, reveal(progress, 0.28, 0.55), alpha=alpha)
    write_text(image, (cx, cy + 35), body, F_TINY, MUTED, reveal(progress, 0.45, 0.55), alpha=alpha, spacing=7)


def scene_three(image: Image.Image, t: float) -> None:
    alpha = scene_alpha(t, 20.0, 40.0)
    if alpha <= 0:
        return
    draw = ImageDraw.Draw(image, "RGBA")
    write_text(image, (W / 2, 95), "核心创新：模型前后都有工程层", F_H1, TEXT, reveal(t, 20.25, 1.2), alpha=alpha)
    write_text(
        image,
        (W / 2, 158),
        "规则预筛 → 上下文控制 → 结构化输出 → 本地过滤 → 统一报告",
        F_BODY,
        MUTED,
        reveal(t, 21.0, 1.25),
        alpha=alpha,
    )

    xs = [195, 500, 805, 1110, 1415, 1720]
    y = 360
    for i, (num, title, body, color) in enumerate(PIPE):
        pipeline_node(image, xs[i], y, num, title, body, color, reveal(t, 22.0 + i * 0.55, 1.1), alpha)
        if i < len(PIPE) - 1:
            arrow(draw, (xs[i] + 142, y), (xs[i + 1] - 142, y), PIPE[i + 1][3], reveal(t, 22.7 + i * 0.55, 0.9), 4, alpha)

    filter_p = reveal(t, 28.3, 1.0)
    rounded_panel(draw, (410, 610, 1510, 850), GREEN, filter_p, alpha=alpha, fill_alpha=80, radius=18)
    write_text(image, (485, 655), "过滤层拦截示例", F_H2, TEXT, reveal(t, 28.65, 0.9), anchor="lm", alpha=alpha)

    chips = [
        ("置信度 0.30", "× 拦截", RED),
        ("未变更行", "× 拦截", RED),
        ("0.88 · 第 42 行", "通过", GREEN),
    ]
    for i, (left, right, color) in enumerate(chips):
        p = reveal(t, 29.7 + i * 1.35, 1.0)
        x = 835
        yy = 720 + i * 48
        yy -= 10 * (1 - ease_out(p))
        draw.rounded_rectangle((x - 195, yy - 22, x + 195, yy + 23), radius=11, fill=rgba(PANEL, int(150 * alpha * p)))
        write_text(image, (x - 150, yy), left, F_SMALL, TEXT, p, anchor="lm", alpha=alpha)
        write_text(image, (x + 310, yy), right, F_BODY_BOLD, color, reveal(t, 30.45 + i * 1.35, 0.55), anchor="lm", alpha=alpha)

    write_text(image, (W / 2, 940), "每条建议都要能解释、能定位、能被校验。", F_H2, TEXT, reveal(t, 35.1, 1.4), alpha=alpha)


def delivery_card(
    image: Image.Image,
    cx: float,
    cy: float,
    title: str,
    body: str,
    color: str,
    progress: float,
    alpha: float,
    *,
    scale: float = 1.0,
) -> None:
    draw = ImageDraw.Draw(image, "RGBA")
    w, h = 390 * scale, 260 * scale
    rounded_panel(draw, (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), color, reveal(progress, 0, 0.55), alpha=alpha, fill_alpha=118, radius=16)
    write_text(image, (cx, cy - 78 * scale), title, F_CARD, color, reveal(progress, 0.24, 0.55), alpha=alpha)
    write_text(image, (cx, cy + 22 * scale), body, F_CARD_BODY, TEXT, reveal(progress, 0.45, 0.65), alpha=alpha, spacing=18)


def scene_four(image: Image.Image, t: float) -> None:
    alpha = scene_alpha(t, 40.0, 58.0)
    if alpha <= 0:
        return
    draw = ImageDraw.Draw(image, "RGBA")
    write_text(image, (W / 2, 95), "同一份报告，进入三个真实使用场景", F_H1, TEXT, reveal(t, 40.25, 1.15), alpha=alpha)

    rounded_panel(draw, (780, 180, 1140, 305), GOLD, reveal(t, 41.2, 1.0), alpha=alpha, fill_alpha=105, radius=16)
    write_text(image, (960, 242), "⑥ 统一报告", F_H2, GOLD, reveal(t, 41.55, 0.85), alpha=alpha)

    cards = [
        (405, "Web 控制台", "粘贴链接即用\n零安装体验\n评委快速查看", BLUE),
        (960, "PR 自动流水线", "创建 PR 自动审查\n结果自动回帖\n团队零操作", AMBER),
        (1515, "编辑器插件", "报告回到 IDE\n一键跳到代码\n开发者直接修复", GREEN),
    ]
    for i, (cx, title, body, color) in enumerate(cards):
        arrow(draw, (960, 305), (cx, 475), color, reveal(t, 42.0 + i * 0.32, 1.0), 5, alpha)
        delivery_card(image, cx, 650, title, body, color, reveal(t, 42.7 + i * 0.75, 1.3), alpha)

    callouts = [
        (405, "给评委和队友"),
        (960, "给团队协作"),
        (1515, "给开发者修复"),
    ]
    for i, (cx, text) in enumerate(callouts):
        pill(image, cx, 850, text, cards[i][3], reveal(t, 47.5 + i * 0.55, 1.0), alpha=alpha)

    write_text(image, (W / 2, 950), "不是三个孤立产品，而是一份结构化结果的三种落地方式。", F_BODY, MUTED, reveal(t, 52.0, 1.35), alpha=alpha)


def scene_five(image: Image.Image, t: float) -> None:
    alpha = reveal(t, 58.0, 1.0)
    if alpha <= 0:
        return
    draw = ImageDraw.Draw(image, "RGBA")
    write_text(image, (W / 2, 105), "总览结束 · 接下来逐个演示", F_H1, TEXT, reveal(t, 58.2, 1.0), alpha=alpha)

    cards = [
        (600, "Web 控制台", "粘贴 PR 链接\n从零生成报告", BLUE, 1.13),
        (960, "PR 自动流水线", "自动审查\n自动回帖", AMBER, 0.78),
        (1320, "编辑器插件", "跳到代码\n直接修复", GREEN, 0.78),
    ]
    for i, (cx, title, body, color, scale) in enumerate(cards):
        p = reveal(t, 59.1 + i * 0.25, 1.0)
        delivery_card(image, cx, 520, title, body, color, p, alpha, scale=scale)
    glow = 0.55 + 0.45 * math.sin(t * 5) ** 2
    draw.rounded_rectangle((370, 355, 830, 685), radius=24, outline=rgba(BLUE, int(210 * alpha * glow)), width=8)

    arrow(draw, (600, 710), (600, 800), BLUE, reveal(t, 61.0, 0.9), 6, alpha)
    write_text(image, (W / 2, 870), "接下来：从 Web 控制台开始实机演示", F_CLOSE, TEXT, reveal(t, 61.5, 1.1), alpha=alpha)
    write_text(image, (W / 2, 935), "粘贴一个 PR 链接，从零到一份报告。", F_BODY, MUTED, reveal(t, 62.5, 1.0), alpha=alpha)


def render_frame(t: float) -> Image.Image:
    image = base_frame(t)
    scene_one(image, t)
    scene_two(image, t)
    scene_three(image, t)
    scene_four(image, t)
    scene_five(image, t)
    return image.convert("RGB")


def run_ffmpeg() -> None:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required to encode preview videos")
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


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    FRAMES.mkdir(parents=True, exist_ok=True)
    for old in FRAMES.glob("frame_*.png"):
        old.unlink()

    frame_count = FPS * DURATION
    for i in range(frame_count):
        render_frame(i / FPS).save(FRAMES / f"frame_{i:04d}.png")
        if i % (FPS * 5) == 0:
            print(f"rendered {i}/{frame_count}")
    render_frame(60.8).save(POSTER)
    run_ffmpeg()
    print(MP4)
    print(GIF)
    print(POSTER)


if __name__ == "__main__":
    main()
