[![MseeP.ai Security Assessment Badge](https://mseep.net/pr/cursortouch-windows-mcp-badge.png)](https://mseep.ai/app/cursortouch-windows-mcp)

<div align="center">
  <h1>🪟 Windows-MCP</h1>

  <a href="https://github.com/CursorTouch/Windows-MCP/blob/main/LICENSE">
    <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
  </a>
  <img src="https://img.shields.io/badge/python-3.13%2B-blue" alt="Python">
  <img src="https://img.shields.io/badge/platform-Windows%207–11-blue" alt="Platform: Windows 7 to 11">
  <img src="https://img.shields.io/github/last-commit/CursorTouch/Windows-MCP" alt="Last Commit">
  <br>
  <a href="https://x.com/CursorTouch">
    <img src="https://img.shields.io/badge/follow-%40CursorTouch-1DA1F2?logo=twitter&style=flat" alt="Follow on Twitter">
  </a>
  <a href="https://discord.com/invite/Aue9Yj2VzS">
    <img src="https://img.shields.io/badge/Join%20on-Discord-5865F2?logo=discord&logoColor=white&style=flat" alt="Join us on Discord">
  </a>

</div>

<br>

**Windows-MCP** is a lightweight, open-source project that enables seamless integration between AI agents and the Windows operating system. Acting as an MCP server bridges the gap between LLMs and the Windows operating system, allowing agents to perform tasks such as **file navigation, application control, UI interaction, QA testing,** and more.

mcp-name: io.github.CursorTouch/Windows-MCP

## Updates
- Added VM support for Windows-MCP. Check (windowsmcp.io)[https://windowsmcp.io/] for more details.
- Windows-MCP reached `2M+ Users` in [Claude Desktop Extensiosn](https://claude.ai/directory). 
- Try out [🪟Windows-Use](https://pypi.org/project/windows-use/), an agent built using Windows-MCP.
- Windows-MCP is now available on [PyPI](https://pypi.org/project/windows-mcp/) (thus supports `uvx windows-mcp`)
- Windows-MCP is added to [MCP Registry](https://github.com/modelcontextprotocol/registry)

### Supported Operating Systems

- Windows 7
- Windows 8, 8.1
- Windows 10
- Windows 11  

## 🎥 Demos

<https://github.com/user-attachments/assets/d0e7ed1d-6189-4de6-838a-5ef8e1cad54e>

<https://github.com/user-attachments/assets/d2b372dc-8d00-4d71-9677-4c64f5987485>

## ✨ Key Features

- **Seamless Windows Integration**  
  Interacts natively with Windows UI elements, opens apps, controls windows, simulates user input, and more.

- **Use Any LLM (Vision Optional)**
   Unlike many automation tools, Windows-MCP doesn't rely on any traditional computer vision techniques or specific fine-tuned models; it works with any LLMs, reducing complexity and setup time.

- **Rich Toolset for UI Automation**  
  Includes tools for basic keyboard, mouse operation and capturing window/UI state.

- **Lightweight & Open-Source**  
  Minimal dependencies and easy setup with full source code available under MIT license.

- **Customizable & Extendable**  
  Easily adapt or extend tools to suit your unique automation or AI integration needs.

- **Real-Time Interaction**  
  Typical latency between actions (e.g., from one mouse click to the next) ranges from **0.2 to 0.9 secs**, and may slightly vary based on the number of active applications and system load, also the inferencing speed of the llm.

- **DOM Mode for Browser Automation**  
  Special `use_dom=True` mode for State-Tool that focuses exclusively on web page content, filtering out browser UI elements for cleaner, more efficient web automation.

## 🛠️Installation

**Note:** When you install this MCP server for the first time it may take a minute or two because of installing the dependencies in `pyproject.toml`. In the first run the server may timeout ignore it and restart it.

### Prerequisites

- Python 3.13+
- UV (Package Manager) from Astra, install with `pip install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`
- `English` as the default language in Windows preferred else disable the `App-Tool` in the MCP Server for Windows with other languages.
<details>
  <summary>Install in Claude Desktop</summary>

  1. Install [Claude Desktop](https://claude.ai/download) and

```shell
npm install -g @anthropic-ai/mcpb
```


  2. Configure the extension:

  **Option A: Install from PyPI (Recommended)**
  
  Use `uvx` to run the latest version directly from PyPI.

  Add this to your `claude_desktop_config.json`:
  ```json
  {
    "mcpServers": {
      "windows-mcp": {
        "command": "uvx",
        "args": [
          "windows-mcp"
        ]
      }
    }
  }
  ```

  **Option B: Install from Source**

  1. Clone the repository:
  ```shell
  git clone https://github.com/CursorTouch/Windows-MCP.git
  cd Windows-MCP
  ```

  2. Add this to your `claude_desktop_config.json`:
  ```json
  {
    "mcpServers": {
      "windows-mcp": {
        "command": "uv",
        "args": [
          "--directory",
          "<path to the windows-mcp directory>",
          "run",
          "windows-mcp"
        ]
      }
    }
  }
  ```



  3. Open Claude Desktop and enjoy! 🥳


  5. Enjoy 🥳.

  **Claude Desktop MSIX (Windows Store)**

  The MSIX-packaged Claude Desktop (Microsoft Store version) virtualizes `%APPDATA%`. This causes two main issues:
  1. The config file is located at: `%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json` (not `%APPDATA%\Claude\`).
  2. Automatic installation from the "Claude Directory" will fail because the `${__dirname}` variable resolves to the incorrect (non-virtualized) path.

  **To configure Windows-MCP on the Windows Store version of Claude:**
  
  You must manually edit the configuration file. Note that Electron apps in the MSIX sandbox do not inherit the system `PATH`, so you must use the **full absolute path** to `uvx.exe` (or `uv.exe`).

  **Option A: Using pre-installed executable**
  1. In a terminal, run: `uv tool install windows-mcp`
  2. Use the generated executable in your config:
  ```json
  {
    "mcpServers": {
      "windows-mcp": {
        "command": "C:\\Users\\<user>\\.local\\bin\\windows-mcp.exe",
        "args": []
      }
    }
  }
  ```

  **Option B: Using uvx**
  ```json
  {
    "mcpServers": {
      "windows-mcp": {
        "command": "C:\\Users\\<user>\\.local\\bin\\uvx.exe",
        "args": ["windows-mcp"]
      }
    }
  }
  ```

  **Option C: Install from Source**
  ```json
  {
    "mcpServers": {
      "windows-mcp": {
        "command": "C:\\Users\\<user>\\.local\\bin\\uv.exe",
        "args": [
          "--directory",
          "C:\\path\\to\\Windows-MCP",
          "run",
          "windows-mcp"
        ]
      }
    }
  }
  ```

  Replace `<user>` with your Windows username. To find the correct paths, run `where uvx`, `where windows-mcp`, or `where uv`. Fully quit Claude Desktop (Tray → Quit) and reopen after saving the config.

  For additional Claude Desktop integration troubleshooting, see the [MCP documentation](https://modelcontextprotocol.io/quickstart/server#claude-for-desktop-integration-issues).
</details>

<details>
  <summary>Install in Perplexity Desktop</summary>

  1. Install [Perplexity Desktop](https://apps.microsoft.com/detail/xp8jnqfbqh6pvf):

  2. Clone the repository.

```shell
git clone https://github.com/CursorTouch/Windows-MCP.git

cd Windows-MCP
```
  
  3. Open Perplexity Desktop:

Go to `Settings->Connectors->Add Connector->Advanced`

  4. Enter the name as `Windows-MCP`, then paste the following JSON in the text area.


  **Option A: Install from PyPI (Recommended)**

  ```json
  {
    "command": "uvx",
    "args": [
      "windows-mcp"
    ]
  }
  ```

  **Option B: Install from Source**

  ```json
  {
    "command": "uv",
    "args": [
      "--directory",
      "<path to the windows-mcp directory>",
      "run",
      "windows-mcp"
    ]
  }
  ```


5. Click `Save` and Enjoy 🥳.

For additional Claude Desktop integration troubleshooting, see the [Perplexity MCP Support](https://www.perplexity.ai/help-center/en/articles/11502712-local-and-remote-mcps-for-perplexity). The documentation includes helpful tips for checking logs and resolving common issues.
</details>

<details>
  <summary> Install in Gemini CLI</summary>

  1. Install Gemini CLI:

```shell
npm install -g @google/gemini-cli
```


  2. Configure the server in `%USERPROFILE%/.gemini/settings.json`:


  3. Navigate to `%USERPROFILE%/.gemini` in File Explorer and open `settings.json`.

  4. Add the `windows-mcp` config in the `settings.json` and save it.

```json
{
  "theme": "Default",
  ...
  "mcpServers": {
    "windows-mcp": {
      "command": "uvx",
      "args": [
        "windows-mcp"
      ]
    }
  }
}
```
*Note: To run from source, replace the command with `uv` and args with `["--directory", "<path>", "run", "windows-mcp"]`.*


  5. Rerun Gemini CLI in terminal. Enjoy 🥳
</details>

<details>
  <summary>Install in Qwen Code</summary>
  1. Install Qwen Code:

```shell
npm install -g @qwen-code/qwen-code@latest
```

   2. Configure the server in `%USERPROFILE%/.qwen/settings.json`:


  3. Navigate to `%USERPROFILE%/.qwen/settings.json`.

  4. Add the `windows-mcp` config in the `settings.json` and save it.

```json
{
  "mcpServers": {
    "windows-mcp": {
      "command": "uvx",
      "args": [
        "windows-mcp"
      ]
    }
  }
}
```
*Note: To run from source, replace the command with `uv` and args with `["--directory", "<path>", "run", "windows-mcp"]`.*


  5. Rerun Qwen Code in terminal. Enjoy 🥳
</details>

<details>
  <summary>Install in Codex CLI</summary>
  1. Install Codex CLI:

```shell
npm install -g @openai/codex
```

  2. Configure the server in `%USERPROFILE%/.codex/config.toml`:

  3. Navigate to `%USERPROFILE%/.codex/config.toml`.

  4. Add the `windows-mcp` config in the `config.toml` and save it.

```toml
[mcp_servers.windows-mcp]
command="uvx"
args=[
  "windows-mcp"
]
```
*Note: To run from source, replace the command with `uv` and args with `["--directory", "<path>", "run", "windows-mcp"]`.*


  5. Rerun Codex CLI in terminal. Enjoy 🥳
</details>

<details>
  <summary>Install in Claude Code</summary>

  1. Install [Claude Code](https://docs.anthropic.com/en/docs/claude-code/overview):

```shell
npm install -g @anthropic-ai/claude-code
```

  2. Configure the server:

  **Option A: Install from PyPI (Recommended)**

  Use `uvx` to run the latest version directly from PyPI.

  ```shell
  claude mcp add --transport stdio windows-mcp -- uvx windows-mcp
  ```

  **Option B: Install from Source**

  1. Clone the repository:
  ```shell
  git clone https://github.com/CursorTouch/Windows-MCP.git
  cd Windows-MCP
  ```

  2. Run the following command in your terminal:
  ```shell
  claude mcp add --transport stdio windows-mcp -- uv --directory "<path>" run windows-mcp
  ```

  *Note: To make the server available across all projects, add `--scope user` to the command.*

  3. Rerun Claude Code in terminal. Enjoy 🥳

  **Note:** On Windows, if you encounter "Connection closed" errors, use the full path to `uvx.exe`:

  ```shell
  claude mcp add --transport stdio windows-mcp -- C:\Users\<user>\.local\bin\uvx.exe windows-mcp
  ```

  To verify the server is registered, run `claude mcp list`. Inside Claude Code, use `/mcp` to check server status.

  **WSL (Windows Subsystem for Linux)**

  If you run Claude Code from WSL, the MCP server must still execute on the Windows side (it needs Windows APIs for UI automation). Use `powershell.exe` as the command to bridge WSL and Windows:

  1. Install `uv` on **Windows** (from a PowerShell terminal):
  ```powershell
  irm https://astral.sh/uv/install.ps1 | iex
  ```

  2. From your **WSL terminal**, register the server:
  ```shell
  claude mcp add windows-mcp --transport stdio -s user -- powershell.exe -Command "C:\Users\<user>\.local\bin\uvx.exe windows-mcp"
  ```

  Replace `<user>` with your Windows username. The `-s user` flag makes the server available across all projects.

  3. Restart Claude Code and verify with `/mcp`.
</details>

---

## 🖥️ Running Windows-MCP

Windows-MCP runs directly on your Windows machine and exposes its tools to the connected MCP client.

```shell
# Runs with stdio transport (default)
uvx windows-mcp

# Or with SSE/Streamable HTTP for network access
uvx windows-mcp --transport sse --host localhost --port 8000
uvx windows-mcp --transport streamable-http --host localhost --port 8000
```

Optional environment variables can be set to customize behavior — see [Environment Variables](#-environment-variables) below.

### Transport Options

| Transport | Flag | Use Case |
|---|---|---|
| `stdio` (default) | `--transport stdio` | Direct connection from MCP clients like Claude Desktop, Cursor, etc. |
| `sse` | `--transport sse --host HOST --port PORT` | Network-accessible via Server-Sent Events |
| `streamable-http` | `--transport streamable-http --host HOST --port PORT` | Network-accessible via HTTP streaming (recommended for production) |

---

## ⚙️ Environment Variables

All variables are optional unless noted. Set them via the `env` key in `claude_desktop_config.json` (or your MCP client's equivalent config).

### Screenshot & Snapshot

| Variable | Default | Description |
|---|---|---|
| `WINDOWS_MCP_SCREENSHOT_SCALE` | `1.0` | Scale factor applied to screenshots before encoding. Accepts a float in the range `0.1`–`1.0`. Useful on high-resolution displays (1440p, 4K) where the default produces images that exceed Claude Desktop's 1 MB tool-result limit. Set to `0.5` to halve both dimensions (quarter the file size). |
| `WINDOWS_MCP_SCREENSHOT_BACKEND` | `auto` | Screenshot capture backend. Accepted values: `auto` (tries dxcam → mss → pillow in order), `dxcam`, `mss`, `pillow`. Use `mss` or `pillow` if `dxcam` is unavailable or causes issues on your GPU. |
| `WINDOWS_MCP_PROFILE_SNAPSHOT` | _(disabled)_ | Set to `1`, `true`, `yes`, or `on` to emit per-stage timing logs for Screenshot/Snapshot calls. Useful for diagnosing slow captures. |
| `WINDOWS_MCP_DISABLE_FLASH` | _(disabled)_ | Set to `1`, `true`, `yes`, or `on` to suppress the orange-red glowing border that briefly highlights the captured area after every screenshot. The flash is rendered on a transparent always-on-top window *after* capture so it never appears in the captured image. |

### Telemetry

| Variable | Default | Description |
|---|---|---|
| `ANONYMIZED_TELEMETRY` | `true` | Set to `false` to disable anonymous usage telemetry. No personal data, tool arguments, or outputs are ever collected regardless of this setting. |

### Debug

| Variable | Default | Description |
|---|---|---|
| `WINDOWS_MCP_DEBUG` | `false` | Set to `1`, `true`, `yes`, or `on` to enable debug mode, which sets the log level to DEBUG for verbose output. Also available as the `--debug` CLI flag. |

**Example `claude_desktop_config.json` configuration:**

```json
{
  "mcpServers": {
    "windows-mcp": {
      "command": "uvx",
      "args": [
        "windows-mcp"
      ],
      "env": {
        "WINDOWS_MCP_SCREENSHOT_SCALE": "0.5",
        "WINDOWS_MCP_SCREENSHOT_BACKEND": "auto",
        "WINDOWS_MCP_PROFILE_SNAPSHOT": "false",
        "ANONYMIZED_TELEMETRY": "true",
        "WINDOWS_MCP_DEBUG": "false"
      }
    }
  }
}
```

---

## 🔨MCP Tools

MCP Client can access the following tools to interact with Windows:

- `Click`: Click on the screen at the given coordinates.
- `Type`: Type text on an element (optionally clears existing text).
- `Scroll`: Scroll vertically or horizontally on the window or specific regions.
- `Move`: Move mouse pointer or drag (set drag=True) to coordinates.
- `Shortcut`: Press keyboard shortcuts (`Ctrl+c`, `Alt+Tab`, etc).
- `Wait`: Pause for a defined duration.
- `Screenshot`: Fast screenshot-first desktop capture with cursor position, active/open windows, and an image. Skips UI tree extraction for speed and should be the default first call when you mainly need visual context. Supports `display=[0]` or `display=[0,1]` to capture specific screens, or `window_name="..."` (fuzzy title match) / `window_pid=12345` (exact process id) to capture just one window's bounding rectangle. The targeted window is brought to the foreground first unless `focus_window=False`. `window_name` / `window_pid` and `display` are mutually exclusive. After capture, a brief orange-red glowing border is drawn over the captured area as a visual confirmation (set `WINDOWS_MCP_DISABLE_FLASH=1` to disable).
- `Snapshot`: Full desktop state capture for workflows that need interactive element ids, scrollable regions, or `use_dom=True` browser extraction. Supports `use_vision=True` for including screenshots, `display=[0]` or `display=[0,1]` for limiting all returned Snapshot information to specific screens, and `window_name` / `window_pid` for limiting capture to a specific window's bounding rectangle (mutually exclusive with `display`).
- `App`: To launch an application from the start menu, resize or move the window and switch between apps.
- `Shell`: To execute PowerShell commands.
- `Scrape`: To scrape the entire webpage for information.
- `MultiSelect`: Select multiple items (files, folders, checkboxes) with optional Ctrl key. Uses bulk label-to-coordinate resolution when labels are provided.
- `MultiEdit`: Enter text into multiple input fields at specified coordinates. Uses bulk label-to-coordinate resolution when labels are provided.
- `Clipboard`: Read or set Windows clipboard content.
- `Process`: List running processes or terminate them by PID or name.
- `Notification`: Send a Windows toast notification with a title and message.
- `Registry`: Read, write, delete, or list Windows Registry values and keys.

### Performance Notes

`MultiSelect` and `MultiEdit` now resolve label-based coordinates in bulk through `Desktop.get_coordinates_from_labels`, which avoids repeated lookups against the desktop tree state.

PR benchmark (mock-based):

- Iterative: `0.003578s`
- Bulk: `0.002238s`
- Improvement: `~37.45%`

In a local Windows benchmark with a synthetic tree state and 35,000 label resolutions per run, the measured results were:

- Iterative: `0.005895s`
- Bulk: `0.002825s`
- Improvement: `52.09%`

In a local desktop runtime check (live Windows state capture path), the measured averages were:

- FindElement-like (`use_vision=False`, `use_annotation=False`): `0.5507s`
- Snapshot-like (`use_vision=True`, `use_annotation=True`): `0.6717s`
- Relative gain for lighter lookup path: `~18.01%`

You can reproduce the comparison with:

```shell
python scripts/benchmark_multi_coordinates.py
```

## 🤝 Connect with Us
Stay updated and join our community:

- 📢 Follow us on [X](https://x.com/CursorTouch) for the latest news and updates

- 💬 Join our [Discord Community](https://discord.com/invite/Aue9Yj2VzS)

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=CursorTouch/Windows-MCP&type=Date)](https://www.star-history.com/#CursorTouch/Windows-MCP&Date)

## 👥 Contributors

Thanks to all the amazing people who have contributed to Windows-MCP! 🎉

<a href="https://github.com/CursorTouch/Windows-MCP/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=CursorTouch/Windows-MCP" />
</a>

We appreciate every contribution, whether it's code, documentation, bug reports, or feature suggestions. Want to contribute? Check out our [Contributing Guidelines](CONTRIBUTING)!

## 🔒 Security

**Important**: Windows-MCP operates with full system access and can perform irreversible operations. Please review our comprehensive security guidelines before deployment.

For detailed security information, including:
- Tool-specific risk assessments
- Deployment recommendations
- Vulnerability reporting procedures
- Compliance and auditing guidelines

Please read our [Security Policy](SECURITY.md).

## 📊 Telemetry

Windows-MCP collects usage data to help improve the MCP server. No personal information, no tool arguments, no outputs are tracked.

To disable telemetry, set `ANONYMIZED_TELEMETRY` to `false` in your MCP client configuration:

```json
{
  "mcpServers": {
    "windows-mcp": {
      "command": "uvx",
      "args": [
        "windows-mcp"
      ],
      "env": {
        "ANONYMIZED_TELEMETRY": "false"
      }
    }
  }
}
```

See the [Environment Variables](#-environment-variables) section for the full list of configurable options.

For detailed information on what data is collected and how it is handled, please refer to the [Telemetry and Data Privacy](SECURITY.md#telemetry-and-data-privacy) section in our Security Policy.

## 📝 Limitations

- Selecting specific sections of the text in a paragraph, as the MCP is relying on a11y tree. (⌛ Working on it.)
- `Type-Tool` is meant for typing text, not programming in IDE because of it types program as a whole in a file. (⌛ Working on it.)
- This MCP server can't be used to play video games 🎮.

## 🪪 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgements

Windows-MCP makes use of several excellent open-source projects that power its Windows automation features:

- [UIAutomation](https://github.com/yinkaisheng/Python-UIAutomation-for-Windows)

- [PyAutoGUI](https://github.com/asweigart/pyautogui)

Huge thanks to the maintainers and contributors of these libraries for their outstanding work and open-source spirit.

## 🤝Contributing

Contributions are welcome! Please see [CONTRIBUTING](CONTRIBUTING) for setup instructions and development guidelines.

Made with ❤️ by [CursorTouch](https://github.com/CursorTouch)

## Citation

```bibtex
@software{
  author       = {CursorTouch},
  title        = {Windows-MCP: Lightweight open-source project for integrating LLM agents with Windows},
  year         = {2024},
  publisher    = {GitHub},
  url={https://github.com/CursorTouch/Windows-MCP}
}
```
