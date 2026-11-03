"""Countdown prompt shown before AI starts a new desktop control period.

The dialog runs in its own process: Tk needs a thread that owns its interpreter,
and a crash or hang there must not take the MCP server with it. Run as a script,
this file shows the dialog and reports the answer as its exit status, so the
child never writes to a stream shared with the stdio transport.
"""

import subprocess
import sys
import threading

# Distinct from 0/1, which a crashed interpreter can also return.
_EXIT_CODES = {"allow": 10, "deny": 11, "timeout": 12}
_lock = threading.Lock()
_process: subprocess.Popen | None = None


def ask(seconds: int) -> str:
    """Show the prompt and return allow, deny, timeout or error."""
    global _process
    with _lock:
        if _process is not None and _process.poll() is None:
            _process.kill()  # A cancelled request left its prompt open.
        process = _process = subprocess.Popen(
            # -I: never import modules from the user's working directory.
            [sys.executable, "-I", __file__, str(seconds)],
            # stdio transport: never share the MCP pipes.
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    try:
        code = process.wait(timeout=seconds + 15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        return "error"
    return next((answer for answer, value in _EXIT_CODES.items() if value == code), "error")


def _show(seconds: int) -> str:
    import ctypes
    import tkinter as tk

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # Sharp text on scaled displays.
    except Exception:
        pass
    answer = "timeout"
    root = tk.Tk()

    def finish(value: str) -> None:
        nonlocal answer
        answer = value
        root.destroy()

    root.title("Windows-MCP")
    root.attributes("-topmost", True)
    root.resizable(False, False)
    root.configure(padx=28, pady=22)
    root.protocol("WM_DELETE_WINDOW", lambda: finish("deny"))
    tk.Label(
        root, text="AI is about to take control of this computer", font=("Segoe UI", 14, "bold")
    ).pack()
    countdown = tk.Label(root, font=("Segoe UI", 11))
    countdown.pack(pady=(8, 2))
    tk.Label(
        root,
        text="If you keep using the computer without answering, you stay in control.",
        font=("Segoe UI", 9),
        fg="#555555",
    ).pack(pady=(0, 14))
    buttons = tk.Frame(root)
    buttons.pack()
    # takefocus=0: answer by click only, so no key is still held when control begins.
    for text, value in (("Allow now", "allow"), ("Deny", "deny")):
        tk.Button(
            buttons,
            text=text,
            width=12,
            takefocus=0,
            font=("Segoe UI", 10),
            command=lambda value=value: finish(value),
        ).pack(side="left", padx=8)

    def tick(left: int) -> None:
        if left <= 0:
            finish("timeout")
            return
        countdown.configure(text=f"Starting in {left} s")
        root.after(1000, tick, left - 1)

    tick(seconds)
    root.update_idletasks()
    x = (root.winfo_screenwidth() - root.winfo_reqwidth()) // 2
    y = (root.winfo_screenheight() - root.winfo_reqheight()) // 3
    root.geometry(f"+{x}+{y}")
    root.mainloop()
    return answer


if __name__ == "__main__":
    sys.exit(_EXIT_CODES[_show(int(sys.argv[1]))])
