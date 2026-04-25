# Repository Guidelines

## Project Structure & Module Organization
Windows-MCP is a Python-based Model Context Protocol (MCP) server designed for Windows desktop automation. The project follows a layered service architecture:

- **Core Service (`src/windows_mcp/`)**:
  - `__main__.py`: Application entry point using **FastMCP**. It initializes core services and registers 16 automation tools.
  - `desktop/`: Orchestrates high-level operations like window management, screenshots, and input simulation.
  - `tree/`: Manages Windows accessibility tree traversal and UI element discovery using multi-threading.
  - `uia/`: Low-level wrapper around the Windows UIAutomation COM API via `comtypes`.
  - `watchdog/`: Monitors UI focus changes to keep the accessibility tree synchronized.
  - `vdm/`: Virtual Desktop Manager for multi-desktop environments.

## Build, Test, and Development Commands
The project uses **UV** as the package manager and **Hatchling** as the build backend.

- **Environment Setup**: `uv sync`
- **Execution**: `uv run windows-mcp`
- **Formatting**: `ruff format .`
- **Linting**: `ruff check .` (use `--fix` for auto-fixing)
- **Testing**: `pytest`
- **Single Test**: `pytest tests/test_filename.py`

## Coding Style & Naming Conventions
- **General**: Follow **PEP 8** (snake_case for functions/variables, PascalCase for classes).
- **Linter/Formatter**: **Ruff** is enforced with a 100-character line limit and double quotes.
- **Documentation**: Use **Google-style docstrings** for all public functions and classes.
- **Type Safety**: **Type hints are required** on all function signatures.

## Testing Guidelines
Tests are located in the `tests/` directory and use **pytest** with **pytest-asyncio**. Ensure that any new tools or features include corresponding test coverage in `tests/`.

## Agent Instructions
- **Screenshots**: Capped at 1920x1080 for token efficiency. Use the `Screenshot` tool for fast visual context and `Snapshot` for heavy UI element identification.
- **UI Interaction**: Coordinate spaces are based on `BoundingRectangle` from UIA to avoid DPI mismatch issues.
- **Browser Automation**: `Snapshot` includes a special `use_dom=True` mode for Chrome, Edge, and Firefox to extract clean web content.
- **Retries**: UI element fetching uses a `THREAD_MAX_RETRIES=3` strategy in the tree service.
