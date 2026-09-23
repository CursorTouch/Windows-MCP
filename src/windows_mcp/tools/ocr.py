"""Ocr tool -- perform pure-local OCR and return coordinates for each line and word."""

import json
from typing import Literal

from fastmcp import Context
from mcp.types import ToolAnnotations

from windows_mcp import ocr
from windows_mcp.infrastructure import with_analytics


def register(mcp, *, get_desktop, get_analytics):
    @mcp.tool(
        name="Ocr",
        description=(
            "Runs pure-local offline OCR with no cloud or AI API calls. Keywords: OCR, "
            "read text, screen text, image text, extract text, text coordinates, click text. "
            "Modes: screen (whole virtual desktop or selected display), region "
            "([left, top, right, bottom]), file (local image path), element (UIA label "
            "from Snapshot, or a region). Results include plain text plus line/word bboxes, "
            "centers and polygons. Screen modes return virtual-desktop coordinates; file mode "
            "returns image coordinates. engine='auto' tries bundled RapidOCR (CPU ONNX), then "
            "optional pytesseract, then optional Windows.Media.Ocr. The first call may take "
            "several seconds while ONNX models load; RapidOCR's default PP-OCRv6 models are "
            "bundled in the wheel, but a custom/missing model may attempt a one-time download."
        ),
        annotations=ToolAnnotations(
            title="Ocr",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Ocr-Tool")
    def ocr_tool(
        mode: Literal["screen", "region", "file", "element"] = "screen",
        path: str | None = None,
        region: list[int] | str | None = None,
        label: int | str | None = None,
        display: list[int] | str | None = None,
        lang: str = "auto",
        engine: Literal["auto", "rapidocr", "tesseract", "windows"] = "auto",
        ctx: Context = None,
    ) -> str:
        try:
            result = ocr.recognize(
                get_desktop(),
                mode=mode,
                path=path,
                region=region,
                label=label,
                display=display,
                lang=lang,
                engine=engine,
            )
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as exc:
            return f"Error: {exc}"
