import json
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

import fitz
from PIL import Image, ImageDraw, ImageFont
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

from metaclass.modules.presentation.schemas import PPTArtifact, PPTSlideImage, PresentationPlan


class PPTSkillAdapter:
    """Boundary for the PPT generation skill.

    This first implementation turns PresentationPlan into a real, simple PPTX
    using the project's existing python-pptx dependency. The structured request
    file is kept so a richer external skill can replace this renderer later.
    """

    font_face = "PingFang SC"

    def prepare_request(
        self,
        *,
        plan: PresentationPlan,
        job_id: str,
        output_dir: Path,
    ) -> PPTArtifact:
        output_dir.mkdir(parents=True, exist_ok=True)
        pptx_path = output_dir / "deck.pptx"
        speaker_scripts_path = output_dir / "speaker_scripts.json"
        self._render_basic_pptx(plan, pptx_path)
        slide_images = self._render_slide_images(plan, pptx_path, output_dir / "slides")
        speaker_scripts_path.write_text(
            json.dumps(
                {
                    "presentation_plan_id": plan.id,
                    "slides": [
                        {
                            "slide_id": slide.id,
                            "order": slide.order,
                            "title": slide.title,
                            "speaker_script": slide.speaker_script,
                        }
                        for slide in plan.slides
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        request_path = output_dir / "skill_request.json"
        request_path.write_text(
            json.dumps(
                {
                    "presentation_plan": plan.model_dump(mode="json"),
                    "expected_output": str(pptx_path),
                    "speaker_scripts_output": str(speaker_scripts_path),
                    "instructions": [
                        "Generate a polished PPTX from presentation_plan.slides.",
                        "Use each slide.speaker_script as speaker notes.",
                        "Use each slide.suggested_visual to choose layout and visuals.",
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return PPTArtifact(
            id=f"ppt_artifact_{uuid4().hex[:12]}",
            job_id=job_id,
            presentation_plan_id=plan.id,
            pptx_path=str(pptx_path),
            skill_request_path=str(request_path),
            slide_images=slide_images,
        )

    def _render_slide_images(
        self,
        plan: PresentationPlan,
        pptx_path: Path,
        output_dir: Path,
    ) -> list[PPTSlideImage]:
        output_dir.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                subprocess.run(
                    [
                        "soffice",
                        "--headless",
                        "--convert-to",
                        "pdf",
                        "--outdir",
                        temp_dir,
                        str(pptx_path),
                    ],
                    check=True,
                    capture_output=True,
                    timeout=60,
                )
                pdf_path = Path(temp_dir) / f"{pptx_path.stem}.pdf"
                if not pdf_path.exists():
                    raise FileNotFoundError(f"Converted PDF not found: {pdf_path}")
                return self._render_pdf_pages(plan, pdf_path, output_dir)
        except (FileNotFoundError, subprocess.SubprocessError, fitz.FileDataError):
            return self._render_placeholder_images(plan, output_dir)

    @staticmethod
    def _render_pdf_pages(
        plan: PresentationPlan,
        pdf_path: Path,
        output_dir: Path,
    ) -> list[PPTSlideImage]:
        document = fitz.open(pdf_path)
        slide_images = []
        try:
            for index, page in enumerate(document):
                if index >= len(plan.slides):
                    break
                slide_plan = plan.slides[index]
                image_path = output_dir / f"slide_{index + 1:03d}.png"
                pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                pixmap.save(image_path)
                slide_images.append(
                    PPTSlideImage(
                        slide_id=slide_plan.id,
                        slide_no=index + 1,
                        image_path=str(image_path),
                        width=pixmap.width,
                        height=pixmap.height,
                    )
                )
        finally:
            document.close()
        if len(slide_images) != len(plan.slides):
            raise fitz.FileDataError("Rendered slide count does not match PresentationPlan")
        return slide_images

    @staticmethod
    def _load_preview_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        for path in [
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/STHeiti Medium.ttc",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "/System/Library/Fonts/Supplemental/Songti.ttc",
        ]:
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                continue
        return ImageFont.load_default()

    @staticmethod
    def _render_placeholder_images(
        plan: PresentationPlan,
        output_dir: Path,
    ) -> list[PPTSlideImage]:
        slide_images = []
        for index, slide_plan in enumerate(plan.slides, start=1):
            image_path = output_dir / f"slide_{index:03d}.png"
            image = Image.new("RGB", (1280, 720), "white")
            draw = ImageDraw.Draw(image)
            title_font = PPTSkillAdapter._load_preview_font(34)
            body_font = PPTSkillAdapter._load_preview_font(24)
            caption_font = PPTSkillAdapter._load_preview_font(20)
            draw.rectangle((0, 0, 28, 720), fill="#D1495B")
            draw.text((70, 60), slide_plan.title[:90], fill="#1F2937", font=title_font)
            y = 140
            for point in slide_plan.key_points[:5]:
                draw.text((90, y), f"- {point[:90]}", fill="#374151", font=body_font)
                y += 48
            draw.rectangle((720, 150, 1190, 500), outline="#D1495B", width=3)
            draw.text((760, 190), "Visual direction", fill="#D1495B", font=caption_font)
            draw.text(
                (760, 230),
                slide_plan.suggested_visual[:120],
                fill="#374151",
                font=caption_font,
            )
            image.save(image_path)
            slide_images.append(
                PPTSlideImage(
                    slide_id=slide_plan.id,
                    slide_no=index,
                    image_path=str(image_path),
                    width=image.width,
                    height=image.height,
                )
            )
        return slide_images

    def _render_basic_pptx(self, plan: PresentationPlan, output: Path) -> None:
        presentation = Presentation()
        presentation.slide_width = Inches(13.333)
        presentation.slide_height = Inches(7.5)
        presentation.core_properties.title = plan.title
        presentation.core_properties.author = "MetaClass"

        for index, slide_plan in enumerate(plan.slides):
            slide = presentation.slides.add_slide(presentation.slide_layouts[6])
            self._add_background(slide, index)
            self._add_title(slide, slide_plan.title, slide_plan.order)
            self._add_key_points(slide, slide_plan.key_points)
            self._add_visual_panel(slide, slide_plan.suggested_visual)
            self._add_script_summary(slide, slide_plan.speaker_script)

        presentation.save(output)

    @staticmethod
    def _add_background(slide, index: int) -> None:
        palette = [
            ("F7F9F7", "1F6F78", "D1495B"),
            ("F8F5F0", "2E4057", "66A182"),
            ("F4F7FB", "3D348B", "F7B801"),
        ][index % 3]
        background, primary, accent = palette
        fill = slide.background.fill
        fill.solid()
        fill.fore_color.rgb = RGBColor.from_string(background)
        bar = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE,
            Inches(0),
            Inches(0),
            Inches(0.18),
            Inches(7.5),
        )
        bar.fill.solid()
        bar.fill.fore_color.rgb = RGBColor.from_string(accent)
        bar.line.fill.background()
        slide._metaclass_primary = primary
        slide._metaclass_accent = accent

    @staticmethod
    def _add_title(slide, title: str, order: int) -> None:
        title_box = slide.shapes.add_textbox(Inches(0.65), Inches(0.45), Inches(9), Inches(0.8))
        frame = title_box.text_frame
        frame.clear()
        paragraph = frame.paragraphs[0]
        paragraph.text = title
        paragraph.font.size = Pt(34)
        paragraph.font.bold = True
        paragraph.font.name = PPTSkillAdapter.font_face
        paragraph.font.color.rgb = RGBColor.from_string(slide._metaclass_primary)

        badge = slide.shapes.add_shape(
            MSO_SHAPE.OVAL,
            Inches(11.6),
            Inches(0.45),
            Inches(0.78),
            Inches(0.78),
        )
        badge.fill.solid()
        badge.fill.fore_color.rgb = RGBColor.from_string(slide._metaclass_primary)
        badge.line.fill.background()
        number = badge.text_frame.paragraphs[0]
        number.text = f"{order:02d}"
        number.alignment = PP_ALIGN.CENTER
        number.font.size = Pt(18)
        number.font.bold = True
        number.font.name = PPTSkillAdapter.font_face
        number.font.color.rgb = RGBColor(255, 255, 255)

    @staticmethod
    def _add_key_points(slide, key_points: list[str]) -> None:
        points = key_points[:5] or ["核心概念", "关键例子", "课堂小结"]
        box = slide.shapes.add_textbox(Inches(0.75), Inches(1.65), Inches(6.25), Inches(4.2))
        frame = box.text_frame
        frame.clear()
        frame.word_wrap = True
        for index, point in enumerate(points):
            paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
            paragraph.text = point
            paragraph.level = 0
            paragraph.font.size = Pt(22 if index == 0 else 18)
            paragraph.font.bold = index == 0
            paragraph.font.name = PPTSkillAdapter.font_face
            paragraph.font.color.rgb = RGBColor.from_string(slide._metaclass_primary)
            paragraph.space_after = Pt(13)

    @staticmethod
    def _add_visual_panel(slide, suggested_visual: str) -> None:
        panel = slide.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE,
            Inches(7.35),
            Inches(1.55),
            Inches(5.2),
            Inches(3.9),
        )
        panel.fill.solid()
        panel.fill.fore_color.rgb = RGBColor(255, 255, 255)
        panel.line.color.rgb = RGBColor.from_string(slide._metaclass_accent)
        panel.line.width = Pt(2)

        label = slide.shapes.add_textbox(Inches(7.75), Inches(1.95), Inches(4.4), Inches(0.45))
        label_frame = label.text_frame
        label_frame.clear()
        label_text = label_frame.paragraphs[0]
        label_text.text = "Visual direction"
        label_text.font.size = Pt(14)
        label_text.font.bold = True
        label_text.font.name = PPTSkillAdapter.font_face
        label_text.font.color.rgb = RGBColor.from_string(slide._metaclass_accent)

        body = slide.shapes.add_textbox(Inches(7.75), Inches(2.55), Inches(4.35), Inches(2.35))
        body_frame = body.text_frame
        body_frame.clear()
        body_frame.word_wrap = True
        paragraph = body_frame.paragraphs[0]
        paragraph.text = suggested_visual
        paragraph.font.size = Pt(17)
        paragraph.font.name = PPTSkillAdapter.font_face
        paragraph.font.color.rgb = RGBColor.from_string(slide._metaclass_primary)

    @staticmethod
    def _add_script_summary(slide, speaker_script: str) -> None:
        footer = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE,
            Inches(0.65),
            Inches(6.35),
            Inches(11.8),
            Inches(0.55),
        )
        footer.fill.solid()
        footer.fill.fore_color.rgb = RGBColor(255, 255, 255)
        footer.line.fill.background()
        frame = footer.text_frame
        frame.clear()
        paragraph = frame.paragraphs[0]
        paragraph.text = f"讲稿摘要：{speaker_script[:90]}"
        paragraph.font.size = Pt(11)
        paragraph.font.name = PPTSkillAdapter.font_face
        paragraph.font.color.rgb = RGBColor(75, 85, 99)
