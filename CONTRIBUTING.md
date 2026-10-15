# Contributing to Windows-MCP

Thank you for your interest in contributing to Windows-MCP! We welcome contributions from the community to help make this project better. This document provides guidelines and instructions for contributing.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
  - [Prerequisites](#prerequisites)
  - [Development Environment Setup](#development-environment-setup)
- [Development Workflow](#development-workflow)
  - [Branching Strategy](#branching-strategy)
  - [Making Changes](#making-changes)
  - [Commit Messages](#commit-messages)
  - [Code Style](#code-style)
- [Testing](#testing)
  - [Running Tests](#running-tests)
  - [Adding Tests](#adding-tests)
- [Pull Requests](#pull-requests)
  - [Before Submitting](#before-submitting)
  - [Pull Request Process](#pull-request-process)
  - [Review Process](#review-process)
- [Documentation](#documentation)
- [Reporting Issues](#reporting-issues)
- [Security Vulnerabilities](#security-vulnerabilities)
- [Getting Help](#getting-help)

## Code of Conduct

By participating in this project, you agree to maintain a respectful and inclusive environment. We expect all contributors to:

- Be respectful and considerate in communication
- Welcome newcomers and help them get started
- Accept constructive criticism gracefully
- Focus on what's best for the community and project

## Getting Started

### Prerequisites

Before you begin, ensure you have:

- **Windows OS**: Windows 10 or 11
- **Python 3.14+**: [Download Python](https://www.python.org/downloads/)
- **UV Package Manager**: Install with `pip install uv` or see [UV documentation](https://github.com/astral-sh/uv)
- **Git**: [Download Git](https://git-scm.com/downloads)
- **A GitHub account**: [Sign up here](https://github.com/join)

### Development Environment Setup

1. **Fork the Repository**
   
   Click the "Fork" button on the [Windows-MCP repository](https://github.com/CursorTouch/Windows-MCP) to create your own copy.

2. **Clone Your Fork**
   
   ```bash
   git clone https://github.com/YOUR_USERNAME/Windows-MCP.git
   cd Windows-MCP
   ```

3. **Add Upstream Remote**
   
   ```bash
   git remote add upstream https://github.com/CursorTouch/Windows-MCP.git
   ```

4. **Install Dependencies**
   
   ```bash
   uv sync --extra dev
   ```

5. **Verify Installation**
   
   ```bash
   uv run windows-mcp --help
   ```

## Development Workflow

### Branching Strategy

- **`main`** branch contains the latest stable code
- Create feature branches from `main` using descriptive names:
  - Features: `feature/add-new-tool`
  - Bug fixes: `fix/click-tool-coordinates`
  - Documentation: `docs/update-readme`
  - Refactoring: `refactor/desktop-service`

### Making Changes

1. **Create a New Branch**
   
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make Your Changes**
   
   - Write clean, readable code
   - Follow the existing code structure
   - Add comments for complex logic
   - Update documentation as needed

3. **Test Your Changes**
   
   - Test manually in a safe environment (VM recommended)
   - Add automated tests if applicable
   - Ensure existing functionality isn't broken

4. **Commit Your Changes**
   
   ```bash
   git add .
   git commit -m "Add feature: description of your changes"
   ```

### Commit Messages

While we don't enforce a strict commit message format, please make your commits informative:

**Good examples:**
- `Add support for multi-monitor setups in State-Tool`
- `Fix Click-Tool coordinate offset on high DPI displays`
- `Update README with Perplexity Desktop installation steps`
- `Refactor Desktop class to improve error handling`

**Avoid:**
- `fix bug`
- `update`
- `changes`

### Code Style

We use **[Ruff](https://github.com/astral-sh/ruff)** for code formatting and linting.

**Key Guidelines:**
- **Line length**: 100 characters maximum
- **Quotes**: Use double quotes for strings
- **Naming conventions**: Follow PEP 8
  - `snake_case` for functions and variables
  - `PascalCase` for classes
  - `UPPER_CASE` for constants
- **Type hints**: Add type annotations to function signatures
- **Docstrings**: Use Google-style docstrings for all public functions and classes

**Example:**

```python
def click_tool(
    loc: list[int],
    button: Literal['left', 'right', 'middle'] = 'left',
    clicks: int = 1
) -> str:
    """Click on UI elements at specific coordinates.
    
    Args:
        loc: List of [x, y] coordinates to click
        button: Mouse button to use (left, right, or middle)
        clicks: Number of clicks (1=single, 2=double, 3=triple)
    
    Returns:
        Confirmation message describing the action performed
    
    Raises:
        ValueError: If loc doesn't contain exactly 2 integers
    """
    if len(loc) != 2:
        raise ValueError("Location must be a list of exactly 2 integers [x, y]")
    # Implementation...
```

**Format Code:**

```bash
uv run --extra dev ruff format .
```

**Run Linter:**

```bash
uv run --extra dev ruff check .
```

## Testing

### Running Tests

If the project has tests (check the `tests/` directory):

```bash
uv run --extra dev pytest
```

Run specific test files:

```bash
uv run --extra dev pytest tests/test_desktop_views.py
```

Run with coverage:

```bash
uv run --extra dev --with pytest-cov pytest --cov=src tests/
```

### Adding Tests

When adding new features:

1. **Create test files** in the `tests/` directory matching the module structure
2. **Write unit tests** for individual functions
3. **Write integration tests** for tool workflows
4. **Use fixtures** for common test setup
5. **Mock external dependencies** (Windows API calls, file system operations)

**Example Test:**

```python
import pytest
from src.desktop.service import Desktop

def test_click_tool_validates_coordinates():
    """Test that click_tool raises ValueError for invalid coordinates."""
    with pytest.raises(ValueError, match="exactly 2 integers"):
        click_tool([100])  # Missing y coordinate
```

## Pull Requests

### Before Submitting

- [ ] Code follows the project's style guidelines
- [ ] All tests pass (if applicable)
- [ ] Documentation is updated (README, docstrings, etc.)
- [ ] Commit messages are clear and descriptive
- [ ] Changes are tested in a safe environment (VM recommended)
- [ ] No sensitive information (API keys, passwords) is included

### Pull Request Process

1. **Update Your Branch**
   
   ```bash
   git fetch upstream
   git rebase upstream/main
   ```

2. **Push to Your Fork**
   
   ```bash
   git push origin feature/your-feature-name
   ```

3. **Create Pull Request**
   
   - Go to the [Windows-MCP repository](https://github.com/CursorTouch/Windows-MCP)
   - Click "New Pull Request"
   - Select your fork and branch
   - Fill out the PR template with:
     - **Description**: What does this PR do?
     - **Motivation**: Why is this change needed?
     - **Testing**: How was this tested?
     - **Screenshots**: If applicable (UI changes, new features)
     - **Related Issues**: Link any related issues

4. **Respond to Feedback**
   
   - Address reviewer comments promptly
   - Make requested changes in new commits
   - Push updates to the same branch

### Review Process

- Maintainers will review your PR within a few days
- You may be asked to make changes or provide clarification
- Once approved, a maintainer will merge your PR
- Your contribution will be acknowledged in release notes

## Documentation

Good documentation is crucial! When contributing:

### Code Documentation

- **Docstrings**: Add to all public functions, classes, and methods
- **Comments**: Explain complex logic or non-obvious decisions
- **Type hints**: Help users and tools understand your code

### User Documentation

Update relevant documentation files:

- **README.md**: For user-facing features or installation changes
- **SECURITY.md**: For security-related changes
- **CONTRIBUTING.md**: For development process changes

### Tool Documentation

When adding or modifying tools:

1. Update the tool's `description` parameter in its module under `src/windows_mcp/tools/`
2. Add appropriate `ToolAnnotations`
3. Update the tools list in `README.md`
4. Update `manifest.json` if needed

## Reporting Issues

Found a bug or have a feature request? Please open an issue!

### Bug Reports

Include:
- **Description**: Clear description of the bug
- **Steps to Reproduce**: Detailed steps to recreate the issue
- **Expected Behavior**: What should happen
- **Actual Behavior**: What actually happens
- **Environment**: Windows version, Python version, MCP client
- **Screenshots/Logs**: If applicable

### Feature Requests

Include:
- **Description**: What feature do you want?
- **Use Case**: Why is this feature needed?
- **Proposed Solution**: How might this be implemented?
- **Alternatives**: Other approaches you've considered

## Security Vulnerabilities

**DO NOT** report security vulnerabilities through public GitHub issues.

Instead, please:
1. Email the maintainers at [jeogeoalukka@gmail.com](mailto:jeogeoalukka@gmail.com)
2. Or use [GitHub Security Advisories](https://github.com/CursorTouch/Windows-MCP/security/advisories)

See our [Security Policy](SECURITY.md) for more details.

## Getting Help

Need help with your contribution?

- **Discord**: Join our [Discord Community](https://discord.com/invite/Aue9Yj2VzS)
- **Twitter/X**: Follow [@CursorTouch](https://x.com/CursorTouch)
- **GitHub Discussions**: Ask questions in [Discussions](https://github.com/CursorTouch/Windows-MCP/discussions)
- **Issues**: Open an issue for technical questions

## Types of Contributions

We welcome many types of contributions:

### Code Contributions

- **New Tools**: Add new MCP tools for Windows automation
- **Bug Fixes**: Fix issues in existing tools
- **Performance Improvements**: Optimize code for speed or efficiency
- **Refactoring**: Improve code structure and maintainability

### Non-Code Contributions

- **Documentation**: Improve README, guides, or docstrings
- **Testing**: Add test cases or improve test coverage
- **Bug Reports**: Report issues with detailed information
- **Feature Requests**: Suggest new features or improvements
- **Community Support**: Help others in Discord or Discussions
- **Translations**: Help translate documentation (future)

## Recognition

Contributors are recognized in:
- GitHub contributors page
- Release notes for significant contributions
- Special mentions for major features or fixes

## License

By contributing to Windows-MCP, you agree that your contributions will be licensed under the [MIT License](LICENSE.md).

---

Thank you for contributing to Windows-MCP! Your efforts help make this project better for everyone. 🙏

Made with ❤️ by the CursorTouch community