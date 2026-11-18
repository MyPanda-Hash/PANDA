#!/usr/bin/env python3
"""读取 PPTX 结构，或生成逐页 PNG 与联系表。"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw
from PIL.Image import Resampling
from pptx import Presentation

EMU_PER_INCH = 914_400


class PptxToolError(RuntimeError):
    """输入文件或预览转换错误。"""


def _enum_name(value: Any) -> str | None:
    return None if value is None else str(getattr(value, "name", value))


def _shape_view(shape: Any, index: int) -> dict[str, Any]:
    result: dict[str, Any] = {
        "index": index,
        "shape_id": shape.shape_id,
        "name": shape.name,
        "shape_type": _enum_name(shape.shape_type),
        "x": round(shape.left / EMU_PER_INCH, 4),
        "y": round(shape.top / EMU_PER_INCH, 4),
        "w": round(shape.width / EMU_PER_INCH, 4),
        "h": round(shape.height / EMU_PER_INCH, 4),
    }
    if getattr(shape, "is_placeholder", False):
        result["placeholder_type"] = _enum_name(shape.placeholder_format.type)
    if getattr(shape, "has_text_frame", False):
        result["text"] = shape.text
        result["paragraphs"] = [
            {
                "text": paragraph.text,
                "level": paragraph.level,
                "alignment": _enum_name(paragraph.alignment),
            }
            for paragraph in shape.text_frame.paragraphs
        ]
    if getattr(shape, "has_table", False):
        result["table"] = [[cell.text for cell in row.cells] for row in shape.table.rows]
    if getattr(shape, "has_chart", False):
        result["chart"] = {
            "chart_type": _enum_name(shape.chart.chart_type),
            "series": [
                {"name": series.name, "values": [str(value) for value in series.values]}
                for series in shape.chart.series
            ],
        }
    if hasattr(shape, "shapes"):
        result["children"] = [
            _shape_view(child, child_index)
            for child_index, child in enumerate(shape.shapes)
        ]
    return result


def _notes_text(slide: Any) -> str:
    if not getattr(slide, "has_notes_slide", False):
        return ""
    try:
        frame = slide.notes_slide.notes_text_frame
        return frame.text if frame is not None else ""
    except (AttributeError, ValueError):
        return ""


def inspect_presentation(input_path: Path) -> dict[str, Any]:
    source = input_path.expanduser().resolve()
    if not source.is_file():
        raise PptxToolError(f"PPTX 不存在：{source}")
    try:
        presentation = Presentation(source)
    except Exception as exc:
        raise PptxToolError(f"无法读取 PPTX：{source}: {exc}") from exc
    properties = presentation.core_properties
    metadata = {
        name: getattr(properties, name)
        for name in (
            "title", "subject", "author", "keywords", "comments",
            "category", "last_modified_by",
        )
    }
    return {
        "path": str(source),
        "layout": {
            "width": round(presentation.slide_width / EMU_PER_INCH, 4),
            "height": round(presentation.slide_height / EMU_PER_INCH, 4),
        },
        "metadata": metadata,
        "slides": [
            {
                "index": slide_index,
                "notes": _notes_text(slide),
                "shapes": [
                    _shape_view(shape, shape_index)
                    for shape_index, shape in enumerate(slide.shapes)
                ],
            }
            for slide_index, slide in enumerate(presentation.slides)
        ],
    }


def _run(command: list[str]) -> None:
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise PptxToolError(f"缺少系统命令：{command[0]}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise PptxToolError(f"命令失败 ({command[0]})：{detail or result.returncode}")


def _contact_sheet(images: list[Path], output: Path, columns: int) -> None:
    width, label, gap = 480, 32, 16
    rendered: list[Image.Image] = []
    try:
        for path in images:
            with Image.open(path) as source:
                page = source.convert("RGB")
            height = max(1, round(page.height * width / page.width))
            rendered.append(page.resize((width, height), Resampling.LANCZOS))
        cell_height = max(image.height for image in rendered) + label
        rows = (len(rendered) + columns - 1) // columns
        sheet = Image.new(
            "RGB",
            (gap + columns * (width + gap), gap + rows * (cell_height + gap)),
            "white",
        )
        draw = ImageDraw.Draw(sheet)
        for index, image in enumerate(rendered):
            row, column = divmod(index, columns)
            left = gap + column * (width + gap)
            top = gap + row * (cell_height + gap)
            sheet.paste(image, (left, top + label))
            draw.text((left, top + 6), f"Slide {index + 1}", fill="black")
        sheet.save(output, format="PNG")
        sheet.close()
    finally:
        for image in rendered:
            image.close()


def render_presentation(
    input_path: Path, output_dir: Path, dpi: int, columns: int
) -> dict[str, Any]:
    if not 72 <= dpi <= 300:
        raise PptxToolError("dpi 必须在 72 到 300 之间")
    if not 1 <= columns <= 8:
        raise PptxToolError("columns 必须在 1 到 8 之间")
    source = input_path.expanduser().resolve()
    if not source.is_file():
        raise PptxToolError(f"PPTX 不存在：{source}")
    destination = output_dir.expanduser().resolve()
    if destination.exists() and any(destination.iterdir()):
        raise PptxToolError(f"输出目录必须为空：{destination}")
    destination.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="doc-nexus-pptx-", dir="/tmp") as raw_temp:
        temp_dir = Path(raw_temp)
        profile = temp_dir / "libreoffice-profile"
        profile.mkdir()
        _run([
            "soffice", "--headless", f"-env:UserInstallation={profile.as_uri()}",
            "--convert-to", "pdf", "--outdir", str(temp_dir), str(source),
        ])
        pdf = temp_dir / f"{source.stem}.pdf"
        if not pdf.is_file():
            raise PptxToolError("LibreOffice 未生成 PDF")
        _run([
            "pdftoppm", "-png", "-r", str(dpi), str(pdf), str(temp_dir / "page"),
        ])
        pages = sorted(
            temp_dir.glob("page-*.png"),
            key=lambda path: int(path.stem.rsplit("-", 1)[1]),
        )
        if not pages:
            raise PptxToolError("pdftoppm 未生成幻灯片图片")
        outputs = []
        for index, page in enumerate(pages, start=1):
            output = destination / f"slide-{index:03d}.png"
            shutil.copy2(page, output)
            outputs.append(output)
        contact_sheet = destination / "contact-sheet.png"
        _contact_sheet(outputs, contact_sheet, columns)
    return {
        "slides": [str(path) for path in outputs],
        "contact_sheet": str(contact_sheet),
        "dpi": dpi,
    }


def _write_json(value: Any, output: Path | None) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if output is None:
        sys.stdout.write(text)
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="读取 PPTX 结构与文本")
    inspect.add_argument("input", type=Path)
    inspect.add_argument("--output", type=Path)
    render = commands.add_parser("render", help="生成逐页 PNG 与联系表")
    render.add_argument("input", type=Path)
    render.add_argument("--output-dir", required=True, type=Path)
    render.add_argument("--dpi", type=int, default=120)
    render.add_argument("--columns", type=int, default=4)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "inspect":
            _write_json(inspect_presentation(args.input), args.output)
        else:
            _write_json(
                render_presentation(args.input, args.output_dir, args.dpi, args.columns),
                None,
            )
    except PptxToolError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
