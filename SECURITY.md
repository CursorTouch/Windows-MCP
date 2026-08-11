# Security Policy

## Overview

Windows-MCP provides powerful automation capabilities that interact directly with your Windows operating system. This document outlines security considerations, best practices, and our commitment to maintaining a secure project.

## ⚠️ CRITICAL WARNING

**READ THIS BEFORE DEPLOYING WINDOWS-MCP**

### Direct Operating System Interaction

Windows-MCP is **NOT** a sandboxed or isolated tool. It interacts **directly with your actual Windows operating system** on behalf of the connected LLM agent. This means:

- **Real System Actions**: Every tool call executes real actions on your physical or virtual Windows machine
- **No Safety Net**: There is no intermediate layer, simulation, or preview mode
- **User Permissions**: The MCP server operates on behalf of the user running it

### Irreversible and Destructive Changes

Many operations performed by Windows-MCP **CANNOT BE UNDONE**:

- **File Deletions**: Files deleted through PowerShell or UI interactions may be permanently lost
- **Data Overwrites**: Text typed with `clear=True` replaces existing content without recovery options
- **System Modifications**: PowerShell commands can modify registry, services, and system configurations
- **Application Actions**: Clicking "Delete", "Yes", or "Confirm" buttons has real consequences
- **No Undo/Rollback**: Unlike text editors or IDEs, most Windows operations don't have an undo feature

### Where NOT to Deploy

**DO NOT** deploy Windows-MCP on systems where you cannot tolerate the risk of:

- ❌ Accidental data loss or corruption
- ❌ Unintended system configuration changes
- ❌ Exposure of sensitive information through screenshots
- ❌ Execution of malicious commands if the LLM is compromised
- ❌ Compliance violations in regulated environments

**Specifically, NEVER deploy on:**

- Production servers or workstations
- Systems containing irreplaceable data
- Machines with access to sensitive databases or networks
- Compliance-regulated environments (healthcare, finance, government)
- Shared systems or multi-user environments without explicit consent
- Any system you don't fully control and can't afford to lose

### Recommended Safe Deployment

For safer experimentation and usage, **strongly consider** deploying Windows-MCP in:

✅ **Virtual Machines (VMs)**
- Use VMware, VirtualBox, Hyper-V, or similar virtualization platforms
- Take snapshots before each session for easy rollback
- Isolate the VM from production networks
- Limit VM access to non-sensitive resources only

✅ **Sandboxed Environments**
- Windows Sandbox (built into Windows 10/11 Pro/Enterprise)
- Containerized Windows environments
- Dedicated test machines with no production data
- Isolated network segments with restricted access

✅ **Dedicated Test Systems**
- Separate physical machines used only for testing
- Systems with regular backups and disaster recovery plans
- Machines that can be wiped and rebuilt without consequence

### Impact Limitation Strategies

If you must use Windows-MCP on a regular system:

1. **Create a Dedicated User Account**: Run the MCP server under a restricted user account with minimal permissions
2. **Regular Backups**: Maintain frequent, verified backups of all important data
3. **Network Isolation**: Disconnect from production networks or use firewall rules
4. **Supervised Operation**: Always monitor the agent's actions in real-time
5. **Disable High-Risk Tools**: Remove or restrict access to Shell and other destructive tools
6. **Test First**: Thoroughly test workflows in a safe environment before production use

## Security Considerations

### System Access Level

Windows-MCP operates with the same permissions as the user running it. This means:

- **Full System Access**: The MCP server can perform any action that the current user can perform
- **No Sandboxing**: Tools execute directly on your Windows system without isolation
- **Persistent Changes**: Actions taken by the MCP server can permanently modify your system state

### Tool-Specific Security Implications

Based on our tool annotations, here's the security profile of each tool:

#### **High-Risk Tools** (Potentially Destructive)

These tools can make permanent changes to your system:

| Tool | Risk | Description |
|------|------|-------------|
| **Shell** | Critical | Can execute arbitrary PowerShell commands, including system modifications, file deletions, and network operations |
| **Click** | High | Can trigger destructive UI actions (delete confirmations, system dialogs) |
| **Type** | High | Can overwrite text, potentially destroying data when `clear=True` |
| **Drag** | High | Can move/reorganize files, potentially overwriting existing files |
| **Shortcut** | High | Can execute destructive keyboard shortcuts (Ctrl+D delete, Alt+F4 close) |

#### **Medium-Risk Tools** (Modifying but Non-Destructive)

These tools modify system state but are generally safe:

| Tool | Risk | Description |
|------|------|-------------|
| **App** | Medium | Launches/manages applications but doesn't modify data |
| **Scroll** | Low | Only changes viewport position |
| **Move** | Low | Only positions mouse cursor |

#### **Low-Risk Tools** (Read-Only)

These tools only read information without making changes:

| Tool | Risk | Description |
|------|------|-------------|
| **Snapshot** | Safe | Only captures desktop state and screenshots |
| **Wait** | Safe | Only pauses execution |
| **Scrape** | Safe* | Fetches web content (*may expose browsing activity) |

## Best Practices

### 1. **Run with Least Privilege**

- Use a standard user account, not an administrator account, when possible
- Avoid running Windows-MCP with elevated privileges unless absolutely necessary
- Consider creating a dedicated user account for automation tasks

### 2. **Trusted LLM Clients Only**

- Only connect Windows-MCP to trusted MCP clients
- Be cautious when using with third-party or experimental LLM applications
- Review the client application's security practices before integration

### 3. **Monitor Tool Usage**

- Regularly review logs to understand what actions are being performed
- Be especially vigilant with high-risk tools (Shell, Click, etc.)
- Set up alerts for unexpected or suspicious activity

### 4. **Network Security**

- The default `stdio` transport has no network exposure — prefer it for local use
- HTTP transports (`sse`, `streamable-http`) enforce authentication for any non-loopback bind address; starting without `--auth-key`, `--oauth-client-id/secret`, or `--allow-insecure-remote` on a non-loopback host is refused at startup
- **CORS is disabled by default** — no `Access-Control-Allow-Origin` headers are emitted, so browsers block cross-origin requests via their own Same-Origin Policy; use `--cors-origins` only if you need a browser-based MCP client and restrict it to the specific origin(s) required
- **DNS rebinding protection** is applied automatically — the server validates the `Host` header against the configured bind address so a DNS rebinding attack cannot be used to reach a localhost-bound server from an external site
- Use localhost / loopback binding (`127.0.0.1`) instead of `0.0.0.0` when network-wide access is not required
- Implement firewall rules to restrict access to the MCP server ports
- Never expose the MCP server to the internet without `--auth-key` or OAuth and TLS (`--ssl-certfile`/`--ssl-keyfile`)

### 5. **Data Protection**

- Be aware that **Snapshot** captures screenshots that may contain sensitive information
- **Scrape** may fetch content from untrusted websites
- Avoid using Windows-MCP in environments with highly sensitive data
- Consider disabling screenshot functionality (`use_vision=False`) when handling confidential information

### 6. **Code Review**

- Review the source code before deployment in production environments
- Audit any custom extensions or modifications
- Keep dependencies up to date to patch known vulnerabilities

### 7. **Backup and Recovery**

- Maintain regular backups before using automation tools
- Test automation workflows in a safe environment first
- Have a recovery plan in case of unintended system changes

## Deployment Recommendations

### **Recommended Use Cases**

- Personal productivity automation on your own machine
- Development and testing environments
- QA automation in isolated test systems
- Controlled demonstrations with supervision

### **Use with Caution**

- Shared workstations or multi-user systems
- Systems with access to production data
- Environments with compliance requirements (HIPAA, PCI-DSS, etc.)
- Automated workflows without human oversight

### **Not Recommended**

- Production servers or critical infrastructure
- Systems handling highly sensitive data (financial, medical, personal)
- Public-facing systems or kiosks
- Environments where destructive actions cannot be tolerated
- Systems without proper backups

## Vulnerability Reporting

We take security vulnerabilities seriously. If you discover a security issue, please follow responsible disclosure practices:

### How to Report

**DO NOT** open a public GitHub issue for security vulnerabilities.

Instead, please report security issues via:

1. **Email**: Send details to the project maintainers at [jeogeoalukka@gmail.com](mailto:jeogeoalukka@gmail.com)
2. **GitHub Security Advisories**: Use the [GitHub Security Advisory](https://github.com/CursorTouch/Windows-MCP/security/advisories) feature (preferred)

### What to Include

Please provide:

- Description of the vulnerability
- Steps to reproduce the issue
- Potential impact assessment
- Suggested fix (if available)
- Your contact information for follow-up

### Response Timeline

- **Initial Response**: Within 48 hours
- **Status Update**: Within 7 days
- **Fix Timeline**: Depends on severity (critical issues prioritized)

We will acknowledge your contribution in the security advisory and release notes (unless you prefer to remain anonymous).

## Security Updates

### Staying Informed

- Watch this repository for security announcements
- Follow [@CursorTouch](https://x.com/CursorTouch) on X for updates
- Join our [Discord Community](https://discord.com/invite/Aue9Yj2VzS) for discussions

### Update Policy

- Security patches will be released as soon as possible
- Critical vulnerabilities will be addressed within 7 days
- Users will be notified via GitHub releases and community channels

## Dependency Security

Windows-MCP relies on several third-party libraries. We:

- Regularly update dependencies to patch known vulnerabilities
- Monitor security advisories for our dependencies
- Use `uv` for reproducible dependency management

### Key Dependencies

- **comtypes / uia**: Low-level Windows UIAutomation COM bindings for UI interaction and accessibility-tree traversal
- **FastMCP**: MCP server framework (stdio, SSE, Streamable HTTP transports)
- **Starlette / Uvicorn**: ASGI layer used by the HTTP transports; provides CORS, TrustedHost, and custom auth middleware
- **httpx**: HTTP client used by the Scrape tool for web content fetching

## Compliance and Auditing

### Logging

Windows-MCP does not implement comprehensive audit logging by default. For compliance-sensitive environments, consider:

- Implementing custom logging middleware
- Using Windows Event Logging for system-level auditing
- Monitoring file system and registry changes

### Telemetry and Data Privacy

- Windows-MCP collects anonymous usage data to help improve the MCP server.
- **We collect:**
  - Tool execution status (success/failure)
  - Execution duration
  - Tool name
  - Client name and version (e.g., Claude Desktop, etc.)
  - Anonymized session IDs
- **We DO NOT collect:**
  - **Tool arguments** (text typed, coordinates, file paths, etc.)
  - **Tool outputs** (screenshots, commands results, page content, etc.)
  - **Personal Information**:
    - **No User Identifiers** beyond a randomly generated UUID stored locally.
- **Privacy Considerations:**
  - **Error Messages**: In rare cases, system error messages (which are captured) may include local file paths if they are part of the OS error description.
  - Telemetry is **enabled by default** to help maintainers understand usage patterns and prioritize features, but can be disabled at any time by setting the `ANONYMIZED_TELEMETRY` environment variable to `false`.
  - Windows-MCP processes all commands locally on your machine.
  - Screenshots and state captures remain strictly on your local system.
  - Web scraping may expose browsing activity to the target websites.

## Tool Annotations Reference

All tools include security-relevant annotations:

- **readOnlyHint**: `true` if the tool only reads data
- **destructiveHint**: `true` if the tool may perform destructive updates
- **idempotentHint**: `true` if repeated calls have no additional effect
- **openWorldHint**: `true` if the tool interacts with external entities

Refer to `src/windows_mcp/__main__.py` and `src/windows_mcp/tools/` for complete tool annotations.

## Disclaimer

**USE AT YOUR OWN RISK**

Windows-MCP is provided "as is" without warranty of any kind. The maintainers are not responsible for:

- Data loss or system damage caused by tool usage
- Security breaches resulting from improper configuration
- Actions performed by LLM agents using this MCP server
- Compliance violations in regulated environments

Users are solely responsible for:

- Ensuring appropriate use in their environment
- Implementing necessary security controls
- Complying with applicable laws and regulations
- Monitoring and auditing tool usage

## License

This security policy is part of the Windows-MCP project, licensed under the MIT License. See [LICENSE](LICENSE.md) for details.