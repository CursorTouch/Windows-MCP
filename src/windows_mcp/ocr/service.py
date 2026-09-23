"""Pure-local OCR service with lazy, cached engines and coordinate output."""

from __future__ import annotations

import asyncio
import io
import json
import math
import threading
import time
from pathlib import Path
from typing import Any, Literal, TypeAlias

from PIL import Image

import windows_mcp.uia as uia

Mode: TypeAlias = Literal["screen", "region", "file", "element"]
Engine: TypeAlias = Literal["auto", "rapidocr", "tesseract", "windows"]

_rapidocr_instance: Any | None = None
_rapidocr_lock = threading.Lock()

__all__ = ["recognize"]


def _coerce_int_list(value: object, name: str) -> list[int] | None:
    """Accept either a real list or the JSON/comma string an LLM may emit."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = [part.strip() for part in value.split(",") if part.strip()]
    elif isinstance(value, (list, tuple)):
        parsed = list(value)
    else:
        raise ValueError(f"{name} must be a list of integers or a JSON array string")

    if not isinstance(parsed, list):
        raise ValueError(f"{name} must decode to a list of integers")

    values: list[int] = []
    for item in parsed:
        if isinstance(item, bool):
            raise ValueError(f"{name} must contain integers, not booleans")
        if isinstance(item, int):
            values.append(item)
            continue
        if isinstance(item, str) and item.strip().lstrip("+-").isdigit():
            values.append(int(item.strip()))
            continue
        raise ValueError(f"{name} must contain only integers")
    return values


def _coerce_int(value: object, name: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("+-").isdigit():
        return int(value.strip())
    raise ValueError(f"{name} must be an integer")


def _normalize_engine(engine: object) -> Engine:
    normalized = str(engine or "auto").strip().casefold()
    aliases = {
        "tesserocr": "tesseract",
        "pytesseract": "tesseract",
        "windows.media.ocr": "windows",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"auto", "rapidocr", "tesseract", "windows"}:
        raise ValueError("engine must be one of: auto, rapidocr, tesseract, windows")
    return normalized  # type: ignore[return-value]


def _normalize_lang(lang: object) -> str:
    return str(lang or "auto").strip()


def _rapidocr_supports_lang(lang: str) -> bool:
    normalized = lang.casefold().replace("_", "-")
    return normalized in {
        "",
        "auto",
        "ch",
        "zh",
        "zh-cn",
        "zh-hans",
        "chi-sim",
        "en",
        "eng",
        "english",
        "chinese",
    }


def _point_list(box: object) -> list[tuple[float, float]]:
    try:
        raw_points = list(box)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ValueError("OCR box is not an iterable polygon") from exc

    points: list[tuple[float, float]] = []
    for raw_point in raw_points:
        try:
            points.append((float(raw_point[0]), float(raw_point[1])))
        except (IndexError, TypeError, ValueError) as exc:
            raise ValueError("OCR box contains an invalid point") from exc
    if not points:
        raise ValueError("OCR box contains no points")
    return points


def _local_bbox(points: object) -> list[int]:
    parsed = _point_list(points)
    left = math.floor(min(point[0] for point in parsed))
    top = math.floor(min(point[1] for point in parsed))
    right = math.ceil(max(point[0] for point in parsed))
    bottom = math.ceil(max(point[1] for point in parsed))
    return [left, top, right, bottom]


def _local_polygon(points: object) -> list[list[int]]:
    return [[round(x), round(y)] for x, y in _point_list(points)]


def _polygon_from_bbox(bbox: list[int]) -> list[list[int]]:
    left, top, right, bottom = bbox
    return [[left, top], [right, top], [right, bottom], [left, bottom]]


def _score(value: object) -> float | None:
    try:
        return round(float(value), 6)
    except TypeError, ValueError:
        return None


def _make_item(text: object, confidence: object, box: object) -> dict[str, Any]:
    bbox = _local_bbox(box)
    return {
        "text": str(text),
        "confidence": _score(confidence),
        "bbox": bbox,
        "center": [(bbox[0] + bbox[2]) // 2, (bbox[1] + bbox[3]) // 2],
        "polygon": _local_polygon(box),
    }


def _union_boxes(boxes: list[list[int]]) -> list[int]:
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def _get_rapidocr_instance() -> Any:
    global _rapidocr_instance
    if _rapidocr_instance is not None:
        return _rapidocr_instance

    with _rapidocr_lock:
        if _rapidocr_instance is not None:
            return _rapidocr_instance
        try:
            from rapidocr import RapidOCR
        except ImportError as exc:
            raise ImportError(
                "RapidOCR backend requires both 'rapidocr' and 'onnxruntime' packages"
            ) from exc

        try:
            _rapidocr_instance = RapidOCR()
        except ImportError as exc:
            raise ImportError(
                "RapidOCR backend requires the 'onnxruntime' package and its CPU provider"
            ) from exc
    return _rapidocr_instance


def _run_rapidocr(image: Image.Image, lang: str) -> list[dict[str, Any]]:
    if not _rapidocr_supports_lang(lang):
        raise ValueError(
            "the bundled RapidOCR model is Chinese+English; use engine='tesseract' "
            "or engine='windows' for other languages"
        )
    try:
        import numpy as np
    except ImportError as exc:
        raise ImportError("RapidOCR backend requires numpy") from exc

    engine = _get_rapidocr_instance()
    result = engine(
        np.asarray(image.convert("RGB")),
        return_word_box=True,
        return_single_char_box=False,
    )
    if result is None:
        return []

    raw_boxes = getattr(result, "boxes", None)
    boxes = list(raw_boxes) if raw_boxes is not None else []
    raw_texts = getattr(result, "txts", None)
    texts = list(raw_texts) if raw_texts is not None else []
    raw_scores = getattr(result, "scores", None)
    scores = list(raw_scores) if raw_scores is not None else []
    raw_word_results = getattr(result, "word_results", None)
    word_results = list(raw_word_results) if raw_word_results is not None else []
    lines: list[dict[str, Any]] = []

    for index, box in enumerate(boxes):
        text = str(texts[index]) if index < len(texts) else ""
        if not text:
            continue
        confidence = scores[index] if index < len(scores) else None
        line = _make_item(text, confidence, box)
        line_words: list[dict[str, Any]] = []
        if index < len(word_results):
            for word in word_results[index] or []:
                if not isinstance(word, (list, tuple)) or len(word) < 3:
                    continue
                word_text = str(word[0])
                if not word_text:
                    continue
                line_words.append(_make_item(word_text, word[1], word[2]))
        line["words"] = line_words
        lines.append(line)
    return lines


def _tesseract_language(lang: str) -> str:
    normalized = lang.casefold().replace("_", "-")
    if normalized in {"", "auto"}:
        return "eng"
    if normalized in {"ch", "zh", "zh-cn", "zh-hans", "chi-sim"}:
        return "chi_sim+eng"
    if normalized in {"zh-tw", "zh-hant", "chi-tra"}:
        return "chi_tra+eng"
    return lang


def _run_tesseract(image: Image.Image, lang: str) -> list[dict[str, Any]]:
    try:
        import pytesseract
        from pytesseract import Output
    except ImportError as exc:
        raise ImportError("pytesseract backend is not installed") from exc

    try:
        pytesseract.get_tesseract_version()
    except Exception as exc:
        raise ImportError("the external tesseract executable is not available") from exc

    try:
        data = pytesseract.image_to_data(
            image,
            lang=_tesseract_language(lang),
            output_type=Output.DICT,
        )
    except Exception as exc:
        raise RuntimeError(f"tesseract OCR failed: {exc}") from exc

    grouped: dict[tuple[int, int, int], list[dict[str, Any]]] = {}
    total = len(data.get("text", []))
    for index in range(total):
        text = str(data["text"][index]).strip()
        if not text:
            continue
        try:
            confidence = float(data["conf"][index])
        except TypeError, ValueError:
            confidence = -1.0
        if confidence < 0:
            continue

        left = int(data["left"][index])
        top = int(data["top"][index])
        width = int(data["width"][index])
        height = int(data["height"][index])
        bbox = [left, top, left + width, top + height]
        word = {
            "text": text,
            "confidence": round(confidence / 100.0, 6),
            "bbox": bbox,
            "center": [(bbox[0] + bbox[2]) // 2, (bbox[1] + bbox[3]) // 2],
            "polygon": _polygon_from_bbox(bbox),
        }
        key = (
            int(data["block_num"][index]),
            int(data["par_num"][index]),
            int(data["line_num"][index]),
        )
        grouped.setdefault(key, []).append(word)

    lines: list[dict[str, Any]] = []
    for key in sorted(grouped):
        words = grouped[key]
        bbox = _union_boxes([word["bbox"] for word in words])
        scores = [word["confidence"] for word in words if word["confidence"] is not None]
        lines.append(
            {
                "text": " ".join(word["text"] for word in words),
                "confidence": round(sum(scores) / len(scores), 6) if scores else None,
                "bbox": bbox,
                "center": [(bbox[0] + bbox[2]) // 2, (bbox[1] + bbox[3]) // 2],
                "polygon": _polygon_from_bbox(bbox),
                "words": words,
            }
        )
    return lines


def _windows_language(lang: str) -> str | None:
    normalized = lang.casefold().replace("_", "-")
    if normalized in {"", "auto"}:
        return None
    if normalized in {"ch", "zh", "zh-cn", "zh-hans", "chi-sim"}:
        return "zh-Hans"
    if normalized in {"zh-tw", "zh-hant", "chi-tra"}:
        return "zh-Hant"
    if normalized in {"en", "eng", "english"}:
        return "en-US"
    return lang


def _run_windows_media_ocr(image: Image.Image, lang: str) -> list[dict[str, Any]]:
    try:
        from winrt.windows.globalization import Language
        from winrt.windows.graphics.imaging import BitmapDecoder
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream
    except ImportError as exc:
        raise ImportError(
            "Windows.Media.Ocr requires winrt-Windows.Media.Ocr, "
            "winrt-Windows.Graphics.Imaging and winrt-Windows.Storage.Streams"
        ) from exc

    buffered = io.BytesIO()
    image.convert("RGB").save(buffered, format="PNG")
    png_bytes = buffered.getvalue()
    buffered.close()

    async def recognize_async() -> list[dict[str, Any]]:
        stream = InMemoryRandomAccessStream()
        writer = DataWriter(stream)
        writer.write_bytes(png_bytes)
        await writer.store_async()
        await writer.flush_async()
        stream.seek(0)
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()

        engine = None
        language_code = _windows_language(lang)
        if language_code:
            language = Language(language_code)
            if OcrEngine.is_language_supported(language):
                engine = OcrEngine.try_create_from_language(language)
        if engine is None:
            engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            raise RuntimeError("Windows.Media.Ocr has no usable language engine")

        result = await engine.recognize_async(bitmap)
        lines: list[dict[str, Any]] = []
        for ocr_line in result.lines:
            words: list[dict[str, Any]] = []
            for ocr_word in ocr_line.words:
                rect = ocr_word.bounding_rect
                bbox = [
                    round(rect.x),
                    round(rect.y),
                    round(rect.x + rect.width),
                    round(rect.y + rect.height),
                ]
                words.append(
                    {
                        "text": str(ocr_word.text),
                        "confidence": None,
                        "bbox": bbox,
                        "center": [(bbox[0] + bbox[2]) // 2, (bbox[1] + bbox[3]) // 2],
                        "polygon": _polygon_from_bbox(bbox),
                    }
                )
            if not words:
                continue
            bbox = _union_boxes([word["bbox"] for word in words])
            lines.append(
                {
                    "text": str(ocr_line.text),
                    "confidence": None,
                    "bbox": bbox,
                    "center": [(bbox[0] + bbox[2]) // 2, (bbox[1] + bbox[3]) // 2],
                    "polygon": _polygon_from_bbox(bbox),
                    "words": words,
                }
            )
        return lines

    return asyncio.run(recognize_async())


def _recognize_local(
    image: Image.Image, engine: Engine, lang: str
) -> tuple[str, list[dict[str, Any]]]:
    chain = [engine] if engine != "auto" else ["rapidocr", "tesseract", "windows"]
    errors: list[str] = []
    for backend in chain:
        try:
            if backend == "rapidocr":
                return backend, _run_rapidocr(image, lang)
            if backend == "tesseract":
                return backend, _run_tesseract(image, lang)
            if backend == "windows":
                return backend, _run_windows_media_ocr(image, lang)
        except Exception as exc:
            errors.append(f"{backend}: {exc}")
    detail = " | ".join(errors) if errors else "unknown error"
    raise RuntimeError(f"All OCR engines failed: {detail}")


def _shift_lines(lines: list[dict[str, Any]], origin_x: int, origin_y: int) -> list[dict[str, Any]]:
    shifted: list[dict[str, Any]] = []
    for line in lines:
        line_bbox = line["bbox"]
        line_polygon = line["polygon"]
        shifted_line = dict(line)
        shifted_line["bbox"] = [
            line_bbox[0] + origin_x,
            line_bbox[1] + origin_y,
            line_bbox[2] + origin_x,
            line_bbox[3] + origin_y,
        ]
        shifted_line["center"] = [
            line["center"][0] + origin_x,
            line["center"][1] + origin_y,
        ]
        shifted_line["polygon"] = [
            [point[0] + origin_x, point[1] + origin_y] for point in line_polygon
        ]
        shifted_words: list[dict[str, Any]] = []
        for word in line.get("words", []):
            bbox = word["bbox"]
            shifted_word = dict(word)
            shifted_word["bbox"] = [
                bbox[0] + origin_x,
                bbox[1] + origin_y,
                bbox[2] + origin_x,
                bbox[3] + origin_y,
            ]
            shifted_word["center"] = [
                word["center"][0] + origin_x,
                word["center"][1] + origin_y,
            ]
            shifted_word["polygon"] = [
                [point[0] + origin_x, point[1] + origin_y] for point in word["polygon"]
            ]
            shifted_words.append(shifted_word)
        shifted_line["words"] = shifted_words
        shifted.append(shifted_line)
    return shifted


def _flatten_words(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    words: list[dict[str, Any]] = []
    for line_index, line in enumerate(lines):
        for word in line.get("words", []):
            item = {key: value for key, value in word.items() if key != "words"}
            item["line_index"] = line_index
            words.append(item)
    return words


def _parse_region(desktop: Any, region: object) -> uia.Rect:
    values = _coerce_int_list(region, "region")
    if values is None or len(values) != 4:
        raise ValueError("region must be [left, top, right, bottom]")
    return desktop.parse_region_selection(values)


def _parse_display(desktop: Any, display: object) -> list[int] | None:
    values = _coerce_int_list(display, "display")
    if values is None:
        return None
    return desktop.parse_display_selection(values)


def _element_rect(desktop: Any, label: int) -> uia.Rect:
    state = getattr(desktop, "desktop_state", None)
    if state is None or getattr(state, "tree_state", None) is None:
        raise ValueError("Desktop state is empty. Please call Snapshot before element OCR.")
    tree_state = state.tree_state
    interactive = list(getattr(tree_state, "interactive_nodes", []))
    scrollable = list(getattr(tree_state, "scrollable_nodes", []))
    if label < 0:
        raise ValueError("label must be a non-negative integer")
    if label < len(interactive):
        node = interactive[label]
    elif label - len(interactive) < len(scrollable):
        node = scrollable[label - len(interactive)]
    else:
        raise IndexError(f"Label {label} out of range")

    bounding_box = getattr(node, "bounding_box", None)
    if bounding_box is None:
        raise ValueError(f"Label {label} has no bounding box")
    left, top, right, bottom = bounding_box.convert_xywh_to_xyxy()
    if right <= left or bottom <= top:
        raise ValueError(f"Label {label} has an empty bounding box")
    return uia.Rect(left, top, right, bottom)


def _input_image(
    desktop: Any,
    *,
    mode: Mode,
    path: str | None,
    region: object,
    label: object,
    display: object,
) -> tuple[Image.Image, list[int], str]:
    if mode == "file":
        if not path:
            raise ValueError("path is required when mode='file'")
        file_path = Path(path).expanduser()
        if not file_path.is_file():
            raise FileNotFoundError(f"image file not found: {file_path}")
        with Image.open(file_path) as opened:
            image = opened.convert("RGB").copy()
        return image, [0, 0], "image"

    capture_rect: uia.Rect | None = None
    if mode == "region":
        capture_rect = _parse_region(desktop, region)
    elif mode == "element":
        parsed_label = _coerce_int(label, "label")
        if parsed_label is not None:
            capture_rect = _element_rect(desktop, parsed_label)
        elif region is not None and region != "":
            capture_rect = _parse_region(desktop, region)
        else:
            raise ValueError("label or region is required when mode='element'")
    elif mode == "screen":
        display_indices = _parse_display(desktop, display)
        if display_indices:
            capture_rect = desktop.get_display_union_rect(display_indices)
    else:
        raise ValueError("mode must be one of: screen, region, file, element")

    image = desktop.get_screenshot(capture_rect)
    if capture_rect is None:
        screen = uia.GetVirtualScreenRect()
        origin = [int(screen.left), int(screen.top)]
    else:
        origin = [int(capture_rect.left), int(capture_rect.top)]
    return image, origin, "screen"


def recognize(
    desktop: Any,
    *,
    mode: Mode = "screen",
    path: str | None = None,
    region: list[int] | str | None = None,
    label: int | str | None = None,
    display: list[int] | str | None = None,
    lang: str = "auto",
    engine: Engine = "auto",
) -> dict[str, Any]:
    """Run offline OCR and return lines, words, text, confidence and bounding boxes."""
    started = time.perf_counter()
    normalized_mode = str(mode).strip().casefold()
    if normalized_mode not in {"screen", "region", "file", "element"}:
        raise ValueError("mode must be one of: screen, region, file, element")
    normalized_engine = _normalize_engine(engine)
    normalized_lang = _normalize_lang(lang)

    image, origin, coordinate_space = _input_image(
        desktop,
        mode=normalized_mode,  # type: ignore[arg-type]
        path=path,
        region=region,
        label=label,
        display=display,
    )
    backend, local_lines = _recognize_local(image, normalized_engine, normalized_lang)
    lines = _shift_lines(local_lines, origin[0], origin[1])
    words = _flatten_words(lines)
    text = "\n".join(line["text"] for line in lines)

    return {
        "ok": True,
        "engine": backend,
        "mode": normalized_mode,
        "coordinate_space": coordinate_space,
        "origin": origin,
        "image_size": {"width": image.width, "height": image.height},
        "text": text,
        "lines": lines,
        "words": words,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }
