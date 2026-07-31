from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

from windows_mcp.desktop import screenshot
from windows_mcp.uia import Rect


def parse_rect(value: str | None) -> Rect | None:
    if not value:
        return None
    parts = [int(part.strip()) for part in value.split(",")]
    if len(parts) != 4:
        raise ValueError("rect must contain left,top,right,bottom")
    return Rect(*parts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", default="mss")
    parser.add_argument("--output", required=True)
    parser.add_argument("--rect")
    args = parser.parse_args()

    os.environ["WINDOWS_MCP_SCREENSHOT_WORKER"] = "1"
    os.environ["WINDOWS_MCP_SCREENSHOT_ISOLATION"] = "0"
    os.environ["WINDOWS_MCP_DISABLE_FLASH"] = "1"
    os.environ["ANONYMIZED_TELEMETRY"] = "false"

    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        image, used_backend = screenshot._capture_in_process(
            parse_rect(args.rect),
            backend=args.backend,
        )
        image.save(output, format="PNG")
        print(
            json.dumps(
                {
                    "status": "ok",
                    "backend": used_backend,
                    "output": str(output),
                    "bytes": output.stat().st_size,
                },
                ensure_ascii=False,
            )
        )
        return 0
    except BaseException as exc:
        traceback.print_exc(file=sys.stderr)
        print(
            json.dumps(
                {
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
