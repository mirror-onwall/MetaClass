import json
import platform
import subprocess
import tempfile
import textwrap
from pathlib import Path
from uuid import uuid4

import fitz
from PIL import Image, ImageDraw, ImageFont, ImageOps
from pptx import Presentation
from pptx.chart.data import ChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.oxml.xmlchemy import OxmlElement
from pptx.util import Inches, Pt

from metaclass.modules.presentation.schemas import PPTArtifact, PPTSlideImage, PresentationPlan
from metaclass.modules.presentation.brand_palette import BRAND_PALETTE


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
                    "planner_skill": str(Path(__file__).with_name("pptx_skill") / "SKILL.md"),
                    "expected_output": str(pptx_path),
                    "speaker_scripts_output": str(speaker_scripts_path),
                    "instructions": [
                        "Generate a polished PPTX from presentation_plan.slides.",
                        "Use each slide.speaker_script as speaker notes.",
                        "Render each slide.layout and slide.visual_payload as the visible structure.",
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

    def prepare_external_pptx(
        self,
        *,
        plan: PresentationPlan,
        job_id: str,
        output_dir: Path,
        pptx_path: Path,
        provider_name: str,
        provider_metadata: dict,
    ) -> PPTArtifact:
        """Package and preview a PPTX produced by an external presentation service."""
        output_dir.mkdir(parents=True, exist_ok=True)
        request_path = output_dir / "skill_request.json"
        request_path.write_text(
            json.dumps(
                {
                    "presentation_plan": plan.model_dump(mode="json"),
                    "provider": provider_name,
                    "provider_metadata": provider_metadata,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        slide_images = self._render_slide_images(
            plan,
            pptx_path,
            output_dir / "slides",
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
        if platform.system().lower() == "darwin":
            # The bundled headless LibreOffice runtime cannot reliably access
            # macOS system CJK fonts and renders Chinese as tofu boxes. Browser
            # previews use our declarative PIL renderer, which loads PingFang
            # directly; the downloadable PPTX remains unchanged.
            return self._render_placeholder_images(plan, output_dir)
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
            # LibreOffice on macOS may classify Chinese glyphs as latin text
            # when importing python-pptx output. Using the CJK-capable face for
            # both font slots prevents it from substituting empty Arial glyphs.
            return "PingFang SC", "PingFang SC"
        if system == "windows":
            return "Microsoft YaHei", "Microsoft YaHei"
        return "Noto Sans CJK SC", "Noto Sans CJK SC"

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
            if slide_plan.elements:
                image = PPTSkillAdapter._render_scene_preview(slide_plan)
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
                continue
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

    @staticmethod
    def _render_scene_preview(slide_plan) -> Image.Image:
        """Render the declarative scene for the browser when LibreOffice is absent."""
        width, height = 1280, 720
        image = Image.new("RGB", (width, height), f"#{slide_plan.background}")
        draw = ImageDraw.Draw(image)
        for element in sorted(slide_plan.elements, key=lambda item: item.z):
            x0, y0 = round(element.x * width), round(element.y * height)
            x1 = round((element.x + element.w) * width)
            y1 = round((element.y + element.h) * height)
            style = element.style
            fill = f"#{style.fill}" if style.fill else None
            outline = f"#{style.line_color}" if style.line_color else None

            if element.type == "shape":
                if element.shape == "oval":
                    draw.ellipse(
                        (x0, y0, x1, y1),
                        fill=fill,
                        outline=outline,
                        width=max(1, round(style.line_width)),
                    )
                else:
                    radius = 16 if element.shape == "rounded_rectangle" else 0
                    draw.rounded_rectangle(
                        (x0, y0, x1, y1),
                        radius=radius,
                        fill=fill,
                        outline=outline,
                        width=max(1, round(style.line_width)),
                    )
                continue
            if element.type == "line":
                draw.line(
                    (x0, y0, x1, y1),
                    fill=outline or f"#{style.color}",
                    width=max(1, round(style.line_width * 2)),
                )
                continue
            if element.type == "image" and element.image_path:
                try:
                    source = Image.open(element.image_path).convert("RGB")
                    rendered = ImageOps.fit(source, (max(1, x1 - x0), max(1, y1 - y0)))
                    image.paste(rendered, (x0, y0))
                except (FileNotFoundError, OSError):
                    draw.rectangle((x0, y0, x1, y1), fill="#E5E7EB", outline="#94A3B8", width=2)
                continue
            if element.type == "text":
                text = element.text or "\n".join(element.items)
                font_size = max(11, round(style.font_size * 1.32))
                clean = " ".join(text.split())
                while True:
                    chars = max(4, round((x1 - x0 - 10) / max(font_size * 0.95, 1)))
                    lines = textwrap.wrap(
                        clean,
                        width=chars,
                        break_long_words=True,
                        break_on_hyphens=False,
                    ) or [""]
                    max_lines = max(1, round((y1 - y0 - 6) / max(font_size * 1.35, 1)))
                    if len(lines) <= max_lines or font_size <= 18:
                        break
                    font_size -= 1
                font = PPTSkillAdapter._load_preview_font(font_size)
                fitted = "\n".join(lines)
                draw.multiline_text(
                    (x0 + 5, y0 + 3),
                    fitted,
                    fill=f"#{style.color}",
                    font=font,
                    spacing=max(3, round(font_size * 0.18)),
                    align=style.align,
                )
                continue
            if element.type in {"table", "chart"}:
                draw.rounded_rectangle(
                    (x0, y0, x1, y1),
                    radius=10,
                    fill=fill or "#F8FAFC",
                    outline=outline or "#94A3B8",
                    width=2,
                )
                label = element.text or ("表格" if element.type == "table" else "图表")
                draw.text(
                    (x0 + 12, y0 + 10),
                    label,
                    fill=f"#{style.color}",
                    font=PPTSkillAdapter._load_preview_font(max(13, round(style.font_size))),
                )
        return image

    def _render_basic_pptx(self, plan: PresentationPlan, output: Path) -> None:
        presentation = Presentation()
        presentation.slide_width = Inches(13.333)
        presentation.slide_height = Inches(7.5)
        presentation.core_properties.title = plan.title
        presentation.core_properties.author = "MetaClass"

        for index, slide_plan in enumerate(plan.slides):
            slide = presentation.slides.add_slide(presentation.slide_layouts[6])
            if slide_plan.elements:
                self._render_scene(slide, slide_plan)
                continue
            self._add_background(slide, index)
            if slide_plan.layout == "hero":
                self._add_hero(
                    slide,
                    slide_plan.title,
                    slide_plan.key_points,
                    slide_plan.visual_payload,
                )
            else:
                self._add_title(slide, slide_plan.title, slide_plan.order)
            if slide_plan.layout == "cards":
                self._add_cards(slide, slide_plan.visual_payload or slide_plan.key_points)
            elif slide_plan.layout == "process":
                self._add_process(slide, slide_plan.visual_payload or slide_plan.key_points)
            elif slide_plan.layout == "comparison":
                self._add_comparison(slide, slide_plan.visual_payload or slide_plan.key_points)
            elif slide_plan.layout == "timeline":
                self._add_timeline(slide, slide_plan.visual_payload or slide_plan.key_points)
            elif slide_plan.layout == "pyramid":
                self._add_pyramid(slide, slide_plan.visual_payload or slide_plan.key_points)
            elif slide_plan.layout == "spotlight":
                self._add_spotlight(slide, slide_plan.visual_payload or slide_plan.key_points)
            elif slide_plan.layout == "two_column":
                self._add_key_points(slide, slide_plan.key_points)
                self._add_visual_panel(
                    slide, slide_plan.suggested_visual, slide_plan.visual_payload
                )
            self._add_script_summary(slide, slide_plan.speaker_script)

        presentation.save(output)

    @staticmethod
    def _render_scene(slide, slide_plan) -> None:
        """Compile an LLM-authored declarative scene into editable PPTX objects."""
        fill = slide.background.fill
        fill.solid()
        fill.fore_color.rgb = RGBColor.from_string(slide_plan.background)
        for element in sorted(slide_plan.elements, key=lambda item: item.z):
            PPTSkillAdapter._add_scene_element(slide, element)

    @staticmethod
    def _add_scene_element(slide, element) -> None:
        sw, sh = 13.333, 7.5
        x, y = Inches(element.x * sw), Inches(element.y * sh)
        w, h = Inches(element.w * sw), Inches(element.h * sh)
        style = element.style
        color = RGBColor.from_string(style.color)
        alignments = {
            "left": PP_ALIGN.LEFT,
            "center": PP_ALIGN.CENTER,
            "right": PP_ALIGN.RIGHT,
        }
        anchors = {
            "top": MSO_ANCHOR.TOP,
            "middle": MSO_ANCHOR.MIDDLE,
            "bottom": MSO_ANCHOR.BOTTOM,
        }

        if element.type == "text":
            box = slide.shapes.add_textbox(x, y, w, h)
            frame = box.text_frame
            frame.clear()
            frame.word_wrap = True
            # Geometry and font fitting are completed before rendering. Letting
            # PowerPoint auto-fit here introduces a second, platform-dependent
            # layout engine and makes previews differ from the exported deck.
            frame.auto_size = None
            frame.margin_left = Inches(0.05)
            frame.margin_right = Inches(0.05)
            frame.margin_top = Inches(0.03)
            frame.margin_bottom = Inches(0.03)
            frame.vertical_anchor = anchors[style.valign]
            text = element.text or "\n".join(element.items)
            for index, value in enumerate(text.splitlines() or [""]):
                paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
                paragraph.text = value
                paragraph.alignment = alignments[style.align]
                PPTSkillAdapter._apply_font(
                    paragraph,
                    size=Pt(style.font_size),
                    bold=style.bold,
                    color=color,
                )
            return

        if element.type == "shape":
            shape_types = {
                "rectangle": MSO_SHAPE.RECTANGLE,
                "rounded_rectangle": MSO_SHAPE.ROUNDED_RECTANGLE,
                "oval": MSO_SHAPE.OVAL,
                "chevron": MSO_SHAPE.CHEVRON,
            }
            shape = slide.shapes.add_shape(shape_types[element.shape], x, y, w, h)
            if style.fill:
                shape.fill.solid()
                shape.fill.fore_color.rgb = RGBColor.from_string(style.fill)
            else:
                shape.fill.background()
            if style.line_color and style.line_width > 0:
                shape.line.color.rgb = RGBColor.from_string(style.line_color)
                shape.line.width = Pt(style.line_width)
            else:
                shape.line.fill.background()
            return

        if element.type == "line":
            connector = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, x, y, x + w, y + h)
            connector.line.color.rgb = RGBColor.from_string(style.line_color or style.color)
            connector.line.width = Pt(max(style.line_width, 1))
            return

        if element.type == "image" and element.image_path:
            image_path = Path(element.image_path)
            if image_path.is_file():
                slide.shapes.add_picture(str(image_path), x, y, w, h)
            return

        if element.type == "table" and element.table_rows:
            rows = element.table_rows
            column_count = max(len(row) for row in rows)
            table = slide.shapes.add_table(len(rows), column_count, x, y, w, h).table
            for row_index, row in enumerate(rows):
                for column_index in range(column_count):
                    cell = table.cell(row_index, column_index)
                    cell.text = row[column_index] if column_index < len(row) else ""
                    cell.fill.solid()
                    cell.fill.fore_color.rgb = RGBColor.from_string(
                        style.fill
                        or ("334E68" if row_index == 0 else "EFF6FF" if row_index % 2 else "F8FAFC")
                    )
                    paragraph = cell.text_frame.paragraphs[0]
                    PPTSkillAdapter._apply_font(
                        paragraph,
                        size=Pt(style.font_size),
                        bold=style.bold or row_index == 0,
                        color=RGBColor(255, 255, 255) if row_index == 0 else color,
                    )
            return

        if element.type == "chart" and element.chart_categories and element.chart_series:
            valid_series = [
                values
                for values in element.chart_series
                if len(values) == len(element.chart_categories)
            ]
            if not valid_series:
                return
            data = ChartData()
            data.categories = element.chart_categories
            for index, values in enumerate(valid_series):
                name = (
                    element.chart_series_names[index]
                    if index < len(element.chart_series_names)
                    else f"Series {index + 1}"
                )
                data.add_series(name, values)
            chart_types = {
                "bar": XL_CHART_TYPE.COLUMN_CLUSTERED,
                "line": XL_CHART_TYPE.LINE,
                "pie": XL_CHART_TYPE.PIE,
                "doughnut": XL_CHART_TYPE.DOUGHNUT,
            }
            chart = slide.shapes.add_chart(chart_types[element.chart_type], x, y, w, h, data).chart
            chart.has_legend = len(valid_series) > 1 or element.chart_type in {"pie", "doughnut"}
            chart.has_title = bool(element.text)
            if element.text:
                chart.chart_title.text_frame.text = element.text

    @staticmethod
    def _add_background(slide, index: int) -> None:
        if index == 0:
            background, primary, accent = (
                BRAND_PALETTE.board,
                BRAND_PALETTE.chalk,
                BRAND_PALETTE.amber,
            )
        else:
            background, primary, accent = (
                BRAND_PALETTE.paper,
                BRAND_PALETTE.ink,
                BRAND_PALETTE.amber,
            )
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
    def _add_visual_panel(
        slide, suggested_visual: str, visual_payload: list[str] | None = None
    ) -> None:
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
        visible_content = "\n".join(f"• {item}" for item in (visual_payload or []))
        paragraph.text = PPTSkillAdapter._fit_text(
            visible_content or suggested_visual,
            max_chars=36,
            max_lines=5,
        )
        PPTSkillAdapter._apply_font(
            paragraph,
            size=Pt(14),
            color=RGBColor.from_string(slide._metaclass_primary),
        )

    @staticmethod
    def _add_cards(slide, items: list[str]) -> None:
        values = (items or ["核心概念", "关键方法", "应用提示"])[:6]
        columns = 3 if len(values) in {3, 6} else 2
        card_width = 3.7 if columns == 3 else 5.55
        for index, item in enumerate(values):
            row, column = divmod(index, columns)
            x = 0.72 + column * (card_width + 0.38)
            y = 1.55 + row * 2.12
            card = slide.shapes.add_shape(
                MSO_SHAPE.ROUNDED_RECTANGLE,
                Inches(x),
                Inches(y),
                Inches(card_width),
                Inches(1.68),
            )
            card.fill.solid()
            card.fill.fore_color.rgb = RGBColor(255, 255, 255)
            card.line.color.rgb = RGBColor.from_string(slide._metaclass_accent)
            frame = card.text_frame
            frame.clear()
            frame.margin_left = frame.margin_right = Inches(0.22)
            paragraph = frame.paragraphs[0]
            paragraph.text = PPTSkillAdapter._fit_text(item, max_chars=24, max_lines=3)
            PPTSkillAdapter._apply_font(
                paragraph,
                size=Pt(16),
                bold=index == 0,
                color=RGBColor.from_string(slide._metaclass_primary),
            )

    @staticmethod
    def _add_hero(slide, title: str, key_points: list[str], items: list[str]) -> None:
        title_box = slide.shapes.add_textbox(Inches(1.05), Inches(1.25), Inches(11.2), Inches(1.45))
        paragraph = title_box.text_frame.paragraphs[0]
        paragraph.text = PPTSkillAdapter._fit_text(title, max_chars=30, max_lines=2)
        paragraph.alignment = PP_ALIGN.CENTER
        PPTSkillAdapter._apply_font(
            paragraph,
            size=Pt(34),
            bold=True,
            color=RGBColor.from_string(slide._metaclass_primary),
        )
        subtitle = slide.shapes.add_textbox(Inches(2.15), Inches(2.9), Inches(9.0), Inches(0.85))
        sub = subtitle.text_frame.paragraphs[0]
        sub.text = PPTSkillAdapter._fit_text(
            (key_points or ["建立整体认识"])[0], max_chars=52, max_lines=2
        )
        sub.alignment = PP_ALIGN.CENTER
        PPTSkillAdapter._apply_font(
            sub, size=Pt(18), color=RGBColor.from_string(slide._metaclass_primary)
        )
        labels = (items or key_points or ["概念", "方法", "应用"])[:4]
        width = min(2.45, 9.8 / max(len(labels), 1))
        total = len(labels) * width + max(0, len(labels) - 1) * 0.25
        start_x = (13.333 - total) / 2
        for index, item in enumerate(labels):
            pill = slide.shapes.add_shape(
                MSO_SHAPE.ROUNDED_RECTANGLE,
                Inches(start_x + index * (width + 0.25)),
                Inches(4.2),
                Inches(width),
                Inches(0.72),
            )
            pill.fill.solid()
            pill.fill.fore_color.rgb = RGBColor.from_string(slide._metaclass_primary)
            pill.line.fill.background()
            text = pill.text_frame.paragraphs[0]
            text.text = PPTSkillAdapter._fit_text(item, max_chars=14, max_lines=1)
            text.alignment = PP_ALIGN.CENTER
            PPTSkillAdapter._apply_font(text, size=Pt(13), bold=True, color=RGBColor(255, 255, 255))

    @staticmethod
    def _add_comparison(slide, items: list[str]) -> None:
        values = (items or ["方案 A", "方案 B", "适用条件 A", "适用条件 B"])[:6]
        midpoint = max(1, (len(values) + 1) // 2)
        groups = [values[:midpoint], values[midpoint:] or ["另一种视角"]]
        for column, group in enumerate(groups):
            x = 0.75 + column * 6.05
            panel = slide.shapes.add_shape(
                MSO_SHAPE.ROUNDED_RECTANGLE,
                Inches(x),
                Inches(1.55),
                Inches(5.55),
                Inches(4.25),
            )
            panel.fill.solid()
            panel.fill.fore_color.rgb = RGBColor(255, 255, 255)
            panel.line.color.rgb = RGBColor.from_string(
                slide._metaclass_accent if column else slide._metaclass_primary
            )
            heading = panel.text_frame.paragraphs[0]
            heading.text = "视角 A" if column == 0 else "视角 B"
            PPTSkillAdapter._apply_font(
                heading,
                size=Pt(18),
                bold=True,
                color=RGBColor.from_string(slide._metaclass_primary),
            )
            for item in group:
                paragraph = panel.text_frame.add_paragraph()
                paragraph.text = PPTSkillAdapter._fit_text(item, max_chars=30, max_lines=2)
                paragraph.space_before = Pt(10)
                PPTSkillAdapter._apply_font(
                    paragraph,
                    size=Pt(15),
                    color=RGBColor.from_string(slide._metaclass_primary),
                )

    @staticmethod
    def _add_timeline(slide, items: list[str]) -> None:
        values = (items or ["起点", "发展", "转折", "结果"])[:5]
        count = len(values)
        start_x, end_x, y = 1.15, 12.15, 3.2
        line = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE,
            Inches(start_x),
            Inches(y),
            Inches(end_x - start_x),
            Inches(0.06),
        )
        line.fill.solid()
        line.fill.fore_color.rgb = RGBColor.from_string(slide._metaclass_accent)
        line.line.fill.background()
        for index, item in enumerate(values):
            x = start_x + index * ((end_x - start_x) / max(count - 1, 1))
            dot = slide.shapes.add_shape(
                MSO_SHAPE.OVAL, Inches(x - 0.18), Inches(y - 0.15), Inches(0.36), Inches(0.36)
            )
            dot.fill.solid()
            dot.fill.fore_color.rgb = RGBColor.from_string(slide._metaclass_primary)
            dot.line.fill.background()
            box_y = 1.65 if index % 2 == 0 else 3.75
            box = slide.shapes.add_textbox(Inches(x - 1.0), Inches(box_y), Inches(2.0), Inches(1.0))
            paragraph = box.text_frame.paragraphs[0]
            paragraph.text = PPTSkillAdapter._fit_text(item, max_chars=16, max_lines=3)
            paragraph.alignment = PP_ALIGN.CENTER
            PPTSkillAdapter._apply_font(
                paragraph,
                size=Pt(14),
                bold=index in {0, count - 1},
                color=RGBColor.from_string(slide._metaclass_primary),
            )

    @staticmethod
    def _add_pyramid(slide, items: list[str]) -> None:
        values = (items or ["基础", "方法", "能力", "目标"])[:5]
        # The prompt contract supplies items bottom-to-top; draw top-to-bottom.
        for index, item in enumerate(reversed(values)):
            level = len(values) - index
            width = 3.0 + index * 1.25
            x = (13.333 - width) / 2
            y = 1.45 + index * 0.88
            layer = slide.shapes.add_shape(
                MSO_SHAPE.TRAPEZOID,
                Inches(x),
                Inches(y),
                Inches(width),
                Inches(0.72),
            )
            layer.fill.solid()
            layer.fill.fore_color.rgb = RGBColor.from_string(
                slide._metaclass_accent if index % 2 == 0 else slide._metaclass_primary
            )
            layer.line.fill.background()
            paragraph = layer.text_frame.paragraphs[0]
            paragraph.text = (
                f"{level}  {PPTSkillAdapter._fit_text(item, max_chars=24, max_lines=1)}"
            )
            paragraph.alignment = PP_ALIGN.CENTER
            PPTSkillAdapter._apply_font(
                paragraph, size=Pt(14), bold=True, color=RGBColor(255, 255, 255)
            )

    @staticmethod
    def _add_spotlight(slide, items: list[str]) -> None:
        values = items or ["本页最重要的结论", "为什么重要", "如何应用"]
        circle = slide.shapes.add_shape(
            MSO_SHAPE.OVAL, Inches(4.55), Inches(1.45), Inches(4.2), Inches(3.55)
        )
        circle.fill.solid()
        circle.fill.fore_color.rgb = RGBColor.from_string(slide._metaclass_primary)
        circle.line.color.rgb = RGBColor.from_string(slide._metaclass_accent)
        circle.line.width = Pt(4)
        main = circle.text_frame.paragraphs[0]
        main.text = PPTSkillAdapter._fit_text(values[0], max_chars=24, max_lines=4)
        main.alignment = PP_ALIGN.CENTER
        PPTSkillAdapter._apply_font(main, size=Pt(22), bold=True, color=RGBColor(255, 255, 255))
        for index, item in enumerate(values[1:4]):
            x = 0.8 if index % 2 == 0 else 9.25
            y = 1.8 + index * 1.35
            box = slide.shapes.add_shape(
                MSO_SHAPE.ROUNDED_RECTANGLE,
                Inches(x),
                Inches(y),
                Inches(3.25),
                Inches(1.0),
            )
            box.fill.solid()
            box.fill.fore_color.rgb = RGBColor(255, 255, 255)
            box.line.color.rgb = RGBColor.from_string(slide._metaclass_accent)
            paragraph = box.text_frame.paragraphs[0]
            paragraph.text = PPTSkillAdapter._fit_text(item, max_chars=20, max_lines=2)
            paragraph.alignment = PP_ALIGN.CENTER
            PPTSkillAdapter._apply_font(
                paragraph,
                size=Pt(14),
                color=RGBColor.from_string(slide._metaclass_primary),
            )

    @staticmethod
    def _add_process(slide, items: list[str]) -> None:
        values = (items or ["理解问题", "分析方法", "形成结论"])[:5]
        count = len(values)
        width = min(2.15, 10.8 / max(count, 1))
        gap = 0.28
        total = count * width + max(0, count - 1) * gap
        start_x = (13.333 - total) / 2
        for index, item in enumerate(values):
            x = start_x + index * (width + gap)
            circle = slide.shapes.add_shape(
                MSO_SHAPE.OVAL,
                Inches(x + width / 2 - 0.28),
                Inches(1.62),
                Inches(0.56),
                Inches(0.56),
            )
            circle.fill.solid()
            circle.fill.fore_color.rgb = RGBColor.from_string(slide._metaclass_accent)
            circle.line.fill.background()
            number = circle.text_frame.paragraphs[0]
            number.text = str(index + 1)
            number.alignment = PP_ALIGN.CENTER
            PPTSkillAdapter._apply_font(
                number, size=Pt(13), bold=True, color=RGBColor(255, 255, 255)
            )
            box = slide.shapes.add_shape(
                MSO_SHAPE.ROUNDED_RECTANGLE,
                Inches(x),
                Inches(2.52),
                Inches(width),
                Inches(2.25),
            )
            box.fill.solid()
            box.fill.fore_color.rgb = RGBColor(255, 255, 255)
            box.line.color.rgb = RGBColor.from_string(slide._metaclass_primary)
            paragraph = box.text_frame.paragraphs[0]
            paragraph.text = PPTSkillAdapter._fit_text(item, max_chars=18, max_lines=4)
            paragraph.alignment = PP_ALIGN.CENTER
            PPTSkillAdapter._apply_font(
                paragraph,
                size=Pt(15),
                bold=index == 0,
                color=RGBColor.from_string(slide._metaclass_primary),
            )

    @staticmethod
    def _add_script_summary(slide, speaker_script: str) -> None:
        footer = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE,
            Inches(0.65),
            Inches(6.5),
            Inches(11.8),
            Inches(0.4),
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
