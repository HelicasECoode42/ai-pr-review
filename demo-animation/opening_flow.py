from __future__ import annotations

import numpy as np
from manim import *


BG = "#0B1020"
PANEL = "#111827"
PANEL_ALT = "#172033"
TEXT = "#E5E7EB"
MUTED = "#9CA3AF"
BLUE = "#60A5FA"
CYAN = "#22D3EE"
GREEN = "#34D399"
AMBER = "#FBBF24"
PINK = "#F472B6"
RED = "#F87171"


class OpeningFlow(Scene):
    """中文开场流程动画：痛点 -> 分析流水线 -> 三端落地。"""

    def construct(self) -> None:
        self.camera.background_color = BG

        title = Text(
            "AI PR Review Assistant",
            font_size=46,
            weight=BOLD,
            color=TEXT,
        )
        subtitle = Text(
            "把 Pull Request diff 转化为可读、可信、可追溯的审查结果",
            font_size=24,
            color=MUTED,
        )
        subtitle.next_to(title, DOWN, buff=0.22)

        self.play(Write(title), FadeIn(subtitle, shift=UP * 0.15), run_time=1.2)
        self.wait(0.45)

        pain_title = Text("大型 PR 的审查难点", font_size=30, weight=BOLD, color=TEXT)
        pain_title.next_to(subtitle, DOWN, buff=0.58)
        pain = VGroup(
            self._pill("文件多", RED),
            self._pill("diff 长", AMBER),
            self._pill("风险隐藏深", PINK),
            self._pill("GitHub 与 IDE 反复切换", BLUE),
        ).arrange(RIGHT, buff=0.34)
        pain.next_to(pain_title, DOWN, buff=0.28)
        note = Text(
            "工具不替代人工 Review，而是先整理上下文、预筛风险、定位代码行。",
            font_size=21,
            color=MUTED,
        )
        note.next_to(pain, DOWN, buff=0.28)
        self.play(FadeIn(pain_title, shift=UP * 0.12))
        self.play(LaggedStart(*[FadeIn(p, shift=DOWN * 0.15) for p in pain], lag_ratio=0.12))
        self.play(FadeIn(note, shift=UP * 0.12))
        self.wait(1.1)
        self.play(
            FadeOut(pain_title, shift=UP * 0.15),
            FadeOut(pain, shift=DOWN * 0.15),
            FadeOut(note, shift=DOWN * 0.15),
            title.animate.to_edge(UP, buff=0.42),
            FadeOut(subtitle),
        )

        flow = self._build_flow()
        self.play(LaggedStart(*[FadeIn(node, scale=0.96) for node in flow["nodes"]], lag_ratio=0.08))
        self.play(LaggedStart(*[Create(edge) for edge in flow["edges"]], lag_ratio=0.08), run_time=1.3)
        self.wait(0.5)

        self._animate_data_pulses(flow["edges"])
        self.wait(0.35)

        quality = VGroup(
            self._pill("结构化输出", BLUE),
            self._pill("置信度过滤", GREEN),
            self._pill("只评论变更行", AMBER),
            self._pill("AI 失败自动降级", PINK),
        ).arrange(RIGHT, buff=0.3)
        quality.next_to(flow["report"], DOWN, buff=0.42)
        quality_title = Text("报告先经过工程化约束，再进入协作场景", font_size=25, color=TEXT)
        quality_title.next_to(quality, UP, buff=0.22)
        self.play(FadeIn(quality_title, shift=UP * 0.12))
        self.play(LaggedStart(*[FadeIn(p, shift=UP * 0.1) for p in quality], lag_ratio=0.08))
        self.wait(0.9)
        self.play(FadeOut(quality_title), FadeOut(quality))

        outputs_title = Text("审查结果回到开发者真正工作的地方", font_size=26, color=TEXT)
        outputs_title.next_to(flow["report"], DOWN, buff=0.45)
        outputs = VGroup(
            self._output_card("GitHub", "自动发布 summary 与 inline comment", BLUE),
            self._output_card("Web Console", "粘贴 PR URL，快速查看完整报告", GREEN),
            self._output_card("VS Code", "Problems / Panel / CodeLens 跳回代码", PINK),
        ).arrange(RIGHT, buff=0.38)
        outputs.next_to(outputs_title, DOWN, buff=0.22)

        out_edges = VGroup(
            self._arrow_between(flow["report"], outputs[0], color=BLUE),
            self._arrow_between(flow["report"], outputs[1], color=GREEN),
            self._arrow_between(flow["report"], outputs[2], color=PINK),
        )

        self.play(FadeIn(outputs_title, shift=UP * 0.12))
        self.play(
            LaggedStart(*[GrowArrow(edge) for edge in out_edges], lag_ratio=0.12),
            LaggedStart(*[FadeIn(card, shift=UP * 0.15) for card in outputs], lag_ratio=0.12),
            run_time=1.2,
        )
        self.wait(0.8)

        close = Text(
            "让每一次代码变更，都自动获得一份可读、可信、可追溯的审查结果。",
            font_size=29,
            weight=BOLD,
            color=TEXT,
        )
        close.to_edge(DOWN, buff=0.35)
        self.play(FadeIn(close, shift=UP * 0.2))
        self.wait(1.2)

    def _build_flow(self) -> dict[str, object]:
        pr = self._node("GitHub PR\nDiff", "变更文件\npatch 片段", BLUE, width=2.45)
        api = self._node("GitHub API", "PR 元信息\n文件列表\n代码补丁", CYAN, width=2.28)
        parser = self._node("Diff Parser", "新增行映射\nhunk 上下文", GREEN, width=2.3)
        rules = self._node("Risk Rules", "鉴权 / SQL\n命令执行\n敏感日志", AMBER, width=2.35)
        context = self._node("Context Pack", "README\n架构说明\nReview 约定", PINK, width=2.45)
        llm = self._node("LLM Reviewer", "结合规则证据\n生成审查建议", BLUE, width=2.45)
        report = self._node("ReviewReport", "风险等级\n代码位置\n原因与建议", GREEN, width=2.65)

        top = VGroup(pr, api, parser, rules).arrange(RIGHT, buff=0.42)
        bottom = VGroup(context, llm, report).arrange(RIGHT, buff=0.5)
        top.move_to(UP * 1.15)
        bottom.move_to(DOWN * 0.85)

        context.align_to(rules, LEFT)
        llm.next_to(context, RIGHT, buff=0.5)
        report.next_to(llm, RIGHT, buff=0.5)

        edges = VGroup(
            self._arrow_between(pr, api),
            self._arrow_between(api, parser),
            self._arrow_between(parser, rules),
            self._arrow_between(parser, context, color=PINK),
            self._arrow_between(rules, llm, color=AMBER),
            self._arrow_between(context, llm, color=PINK),
            self._arrow_between(llm, report, color=GREEN),
        )

        nodes = VGroup(pr, api, parser, rules, context, llm, report)
        return {"nodes": nodes, "edges": edges, "report": report}

    def _node(self, title: str, body: str, color: str, width: float = 2.35) -> VGroup:
        box = RoundedRectangle(
            width=width,
            height=1.16,
            corner_radius=0.12,
            stroke_width=2.2,
            stroke_color=color,
            fill_color=PANEL,
            fill_opacity=0.92,
        )
        title_mob = Text(title, font_size=22, weight=BOLD, color=TEXT, line_spacing=0.8)
        body_mob = Text(body, font_size=14, color=MUTED, line_spacing=0.75)
        label = VGroup(title_mob, body_mob).arrange(DOWN, buff=0.11)
        label.move_to(box.get_center())
        return VGroup(box, label)

    def _output_card(self, title: str, body: str, color: str) -> VGroup:
        box = RoundedRectangle(
            width=3.25,
            height=0.92,
            corner_radius=0.12,
            stroke_width=2,
            stroke_color=color,
            fill_color=PANEL_ALT,
            fill_opacity=0.96,
        )
        title_mob = Text(title, font_size=21, weight=BOLD, color=TEXT)
        body_mob = Text(body, font_size=13, color=MUTED)
        label = VGroup(title_mob, body_mob).arrange(DOWN, buff=0.08)
        label.move_to(box.get_center())
        return VGroup(box, label)

    def _pill(self, label: str, color: str) -> VGroup:
        text = Text(label, font_size=21, color=TEXT)
        box = RoundedRectangle(
            width=text.width + 0.52,
            height=0.48,
            corner_radius=0.2,
            stroke_color=color,
            stroke_width=1.8,
            fill_color=PANEL_ALT,
            fill_opacity=0.95,
        )
        text.move_to(box.get_center())
        return VGroup(box, text)

    def _arrow_between(self, start: Mobject, end: Mobject, color: str = CYAN) -> Arrow:
        direction = end.get_center() - start.get_center()
        unit = direction / max(float(np.linalg.norm(direction)), 0.001)
        return Arrow(
            start=start.get_center() + unit * 1.15,
            end=end.get_center() - unit * 1.15,
            buff=0.0,
            stroke_width=3.2,
            max_tip_length_to_length_ratio=0.16,
            color=color,
        )

    def _animate_data_pulses(self, edges: VGroup) -> None:
        pulses = []
        for edge in edges:
            dot = Dot(radius=0.055, color=WHITE)
            dot.move_to(edge.get_start())
            pulses.append(MoveAlongPath(dot, edge))
        self.play(LaggedStart(*pulses, lag_ratio=0.1), run_time=1.45)
