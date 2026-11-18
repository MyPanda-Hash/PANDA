"""Build-time proof that the host can really render a PPTX preview.

Uploaded and generated presentations require a working Impress filter.
This check builds a one-slide deck, runs the ordinary document preview
conversion path, and fails the build when no PDF comes back.

Run directly: ``python backend/scripts/check-pptx-preview.py``
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402
from pptx import Presentation  # noqa: E402

from app.services import doc_preview  # noqa: E402


def _minimal_deck() -> bytes:
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[0])
    slide.shapes.title.text = "preview check"
    buffer = io.BytesIO()
    presentation.save(buffer)
    return buffer.getvalue()


def _check_dashi_export() -> None:
    """真实导出回读并渲染，保护可见内容、裁剪边界与半透明文字完整性。"""
    skill = Path(__file__).resolve().parents[1] / "app/builtin_skills/dashi-ppt"
    with tempfile.TemporaryDirectory(prefix="dashi-export-check-") as directory:
        env = {**os.environ, "PI_WORKSPACE_ROOT": directory}
        root = Path(subprocess.check_output(
            [sys.executable, str(skill / "scripts/prepare_runtime.py")],
            env=env, text=True, timeout=30,
        ).strip())
        output = Path(directory) / "overflow.pptx"
        script = """
import { chromium } from 'playwright-core';
import { exportEditablePptxFromPage } from './packages/html-deck-to-pptx/src/editable.mjs';
const browser = await chromium.launch({executablePath:process.env.CHROME_PATH, args:['--no-sandbox']});
try {
  const page = await browser.newPage({viewport:{width:1920,height:1080}});
  await page.setContent(`<style>
    body{margin:0} .slide{position:relative;width:1920px;height:1080px}
    .anchor{position:absolute;left:100px;top:200px;width:0;height:0}
    span{position:absolute;white-space:nowrap;font:32px Arial}
  </style><div id="deck"><section class="slide active">
    <div class="anchor"><span>visible anchor</span></div>
    <div class="anchor" style="top:300px;overflow:hidden"><span>clipped anchor</span></div>
    <div class="anchor" style="top:400px;display:none"><span>hidden anchor</span></div>
    <div class="anchor" style="top:500px;left:-100px"><span style="left:200px">visible overflow</span></div>
    <span style="left:100px;top:650px;font:24px monospace;letter-spacing:2.4px;color:rgba(0,0,0,.5)">01 / 01</span>
    <span style="left:100px;top:750px;font:24px monospace;letter-spacing:2.4px">01 / 01</span>
  </section></div>`);
  await exportEditablePptxFromPage(page,{outFile:process.argv[2]});
} finally {
  await browser.close();
}
"""
        subprocess.run(
            ["node", "--input-type=module", "-", str(output)],
            input=script, text=True, cwd=root / "project", env=env,
            check=True, timeout=60,
        )
        deck = Presentation(output)
        text = "\n".join(
            shape.text for slide in deck.slides for shape in slide.shapes
            if shape.has_text_frame
        )
        assert len(deck.slides) == 1
        assert "visible anchor" in text and "visible overflow" in text, text
        assert "clipped anchor" not in text and "hidden anchor" not in text, text
        pdf = doc_preview.convert_presentation_to_pdf(output.read_bytes(), output.name)
        png = doc_preview.render_pdf_pages_to_png(pdf, max_pages=1, scale=120 / 72)[0]
        with Image.open(io.BytesIO(png)) as image:
            glyph_counts = []
            for top in (645, 745):
                mask = image.crop((90, top, 600, top + 60)).convert("L").point(
                    lambda value: 255 if value < 230 else 0
                )
                columns, _ = mask.getprojection()
                glyph_counts.append(sum(
                    bool(on) and (index == 0 or not columns[index - 1])
                    for index, on in enumerate(columns)
                ))
            assert glyph_counts == [5, 5], f"页码字符被裁切：{glyph_counts}"
    print("OK: Dashi export preserves visible/clipped boundaries and all translucent text glyphs")


def main() -> int:
    if not doc_preview.presentation_renderer_available():
        print(
            "FAIL: no LibreOffice/soffice executable; PPTX previews cannot be generated",
            file=sys.stderr,
        )
        return 1
    try:
        pdf = doc_preview.convert_presentation_to_pdf(
            _minimal_deck(), "preview-check.pptx"
        )
    except doc_preview.LibreOfficeUnavailable as exc:
        print(
            f"FAIL: LibreOffice present but unusable ({exc}); "
            "the Impress filter is likely missing (install libreoffice-impress)",
            file=sys.stderr,
        )
        return 1
    except doc_preview.DocumentConversionError as exc:
        print(
            f"FAIL: PPTX conversion produced no usable PDF ({exc}); "
            "verify libreoffice-impress is installed",
            file=sys.stderr,
        )
        return 1
    if not pdf.startswith(b"%PDF-"):
        print("FAIL: conversion output is not a PDF", file=sys.stderr)
        return 1
    pages = doc_preview.render_pdf_pages_to_png(pdf, max_pages=2)
    if not pages or not pages[0].startswith(b"\x89PNG"):
        print("FAIL: PDF could not be rasterised to PNG", file=sys.stderr)
        return 1
    print(f"OK: PPTX preview pipeline works (pdf={len(pdf)} bytes, pages={len(pages)})")
    _check_dashi_export()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
