import json
import platform
import subprocess
import tempfile
import textwrap
from pathlib import Path
from uuid import uuid4

import fitz
from PIL import Image, ImageDraw, ImageFont
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.oxml.ns import qn
from pptx.oxml.xmlchemy import OxmlElement
from pptx.util import Inches, Pt

from metaclass.modules.presentation.schemas import PPTArtifact, PPTSlideImage, PresentationPlan


class PPTSkillAdapter:
    """Boundary for the PPT generation skill.

    This first implementation turns PresentationPlan into a real, simple PPTX
    using the project's existing python-pptx dependency. The structured request
    file is kept so a richer external skill can replace this renderer later.
    """

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
                libreoffice_profile = Path(temp_dir) / "lo_profile"
                libreoffice_profile.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    [
                        "soffice",
                        "--headless",
                        f"-env:UserInstallation={libreoffice_profile.as_uri()}",
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
        except (FileNotFoundError, subprocess.SubprocessError, fitz.FileDataError) as exc:
            (output_dir.parent / "render_error.txt").write_text(
                PPTSkillAdapter._render_error_message(exc),
                encoding="utf-8",
            )
            return self._render_placeholder_images(plan, output_dir)

    @staticmethod
    def _render_error_message(exc: Exception) -> str:
        message = f"PPTX to slide image rendering fell back to PIL placeholder: {exc}"
        if isinstance(exc, subprocess.CalledProcessError):
            stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
            stdout = exc.stdout.decode("utf-8", errors="replace") if exc.stdout else ""
            details = "\n".join(part for part in [stdout.strip(), stderr.strip()] if part)
            if details:
                message = f"{message}\n{details}"
        return message

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
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansSC-Regular.otf",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simhei.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/STHeiti Medium.ttc",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "/System/Library/Fonts/Supplemental/Songti.ttc",
            "/System/Library/Fonts/Helvetica.ttc",
        ]:
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                continue
        return ImageFont.load_default()

    @staticmethod
    def _font_faces() -> tuple[str, str]:
        system = platform.system().lower()
        if system == "darwin":
            return "Arial", "PingFang SC"
        if system == "windows":
            return "Arial", "Microsoft YaHei"
        return "DejaVu Sans", "Noto Sans CJK SC"

    @staticmethod
    def _apply_font(
        paragraph,
        *,
        size: Pt,
        color: RGBColor,
        bold: bool = False,
    ) -> None:
        latin_font, cjk_font = PPTSkillAdapter._font_faces()
        paragraph.font.size = size
        paragraph.font.bold = bold
        paragraph.font.name = latin_font
        paragraph.font.color.rgb = color

        run = paragraph.runs[0] if paragraph.runs else paragraph.add_run()
        run.font.name = latin_font
        run.font.size = size
        run.font.bold = bold
        run.font.color.rgb = color
        rpr = run._r.get_or_add_rPr()
        for tag, font_name in {
            "a:latin": latin_font,
            "a:ea": cjk_font,
            "a:cs": latin_font,
        }.items():
            element = rpr.find(qn(tag))
            if element is None:
                element = OxmlElement(tag)
                rpr.append(element)
            element.set("typeface", font_name)

    @staticmethod
    def _fit_text(text: str, *, max_chars: int, max_lines: int) -> str:
        clean = " ".join(text.split())
        if not clean:
            return ""
        lines = textwrap.wrap(
            clean,
            width=max_chars,
            max_lines=max_lines,
            placeholder="...",
            break_long_words=True,
            break_on_hyphens=False,
        )
        return "\n".join(lines) if lines else clean[:max_chars]

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
            title = PPTSkillAdapter._fit_text(slide_plan.title, max_chars=42, max_lines=2)
            draw.multiline_text(
                (70, 54),
                title,
                fill="#1F2937",
                font=title_font,
                spacing=8,
            )
            y = 150
            for point in slide_plan.key_points[:5]:
                text = PPTSkillAdapter._fit_text(point, max_chars=46, max_lines=2)
                bullet = f"- {text}"
                draw.multiline_text(
                    (90, y),
                    bullet,
                    fill="#374151",
                    font=body_font,
                    spacing=7,
                )
                y += 38 * (bullet.count("\n") + 1) + 14
                if y > 480:
                    break
            draw.rectangle((720, 150, 1190, 500), outline="#D1495B", width=3)
            draw.text((760, 190), "Visual direction", fill="#D1495B", font=caption_font)
            visual = PPTSkillAdapter._fit_text(
                slide_plan.suggested_visual,
                max_chars=34,
                max_lines=8,
            )
            draw.multiline_text(
                (760, 230),
                visual,
                fill="#374151",
                font=caption_font,
                spacing=7,
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
        paragraph.text = PPTSkillAdapter._fit_text(title, max_chars=42, max_lines=2)
        PPTSkillAdapter._apply_font(
            paragraph,
            size=Pt(30),
            bold=True,
            color=RGBColor.from_string(slide._metaclass_primary),
        )

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
        PPTSkillAdapter._apply_font(
            number,
            size=Pt(18),
            bold=True,
            color=RGBColor(255, 255, 255),
        )

    @staticmethod
    def _add_key_points(slide, key_points: list[str]) -> None:
        points = key_points[:5] or ["核心概念", "关键例子", "课堂小结"]
        box = slide.shapes.add_textbox(Inches(0.75), Inches(1.55), Inches(5.95), Inches(4.35))
        frame = box.text_frame
        frame.clear()
        frame.word_wrap = True
        for index, point in enumerate(points):
            paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
            paragraph.text = PPTSkillAdapter._fit_text(point, max_chars=50, max_lines=2)
            paragraph.level = 0
            PPTSkillAdapter._apply_font(
                paragraph,
                size=Pt(18 if index == 0 else 15),
                bold=index == 0,
                color=RGBColor.from_string(slide._metaclass_primary),
            )
            paragraph.space_after = Pt(8)

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
        PPTSkillAdapter._apply_font(
            label_text,
            size=Pt(14),
            bold=True,
            color=RGBColor.from_string(slide._metaclass_accent),
        )

        body = slide.shapes.add_textbox(Inches(7.75), Inches(2.55), Inches(4.35), Inches(2.35))
        body_frame = body.text_frame
        body_frame.clear()
        body_frame.word_wrap = True
        paragraph = body_frame.paragraphs[0]
        paragraph.text = PPTSkillAdapter._fit_text(
            suggested_visual,
            max_chars=36,
            max_lines=5,
        )
        PPTSkillAdapter._apply_font(
            paragraph,
            size=Pt(14),
            color=RGBColor.from_string(slide._metaclass_primary),
        )

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
        paragraph.text = PPTSkillAdapter._fit_text(
            f"讲稿摘要：{speaker_script}",
            max_chars=92,
            max_lines=1,
        )
        PPTSkillAdapter._apply_font(
            paragraph,
            size=Pt(11),
            color=RGBColor(75, 85, 99),
        )
