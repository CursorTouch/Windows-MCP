import time
from typing import Literal


__all__ = ["list_processes", "kill_process"]

#: Window over which CPU usage is measured. psutil's ``cpu_percent()`` reports
#: usage since the previous call on the same process, and a meaningless 0.0 on
#: the first one, so a single pass shows every process idle.
CPU_SAMPLE_SECONDS = 0.25


def list_processes(
    name: str | None = None,
    sort_by: Literal["memory", "cpu", "name"] = "memory",
    limit: int = 20,
) -> str:
    import psutil
    from tabulate import tabulate

    candidates = []
    for p in psutil.process_iter(["pid", "name", "memory_info"]):
        # PID 0 is the System Idle Process: its "CPU" is idle time summed over
        # every core, so it would top any CPU ranking. It is not a real process.
        if p.info["pid"] == 0:
            continue
        try:
            p.cpu_percent(None)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        candidates.append(p)
    time.sleep(CPU_SAMPLE_SECONDS)

    procs = []
    for p in candidates:
        try:
            info = p.info
            mem_mb = info["memory_info"].rss / (1024 * 1024) if info["memory_info"] else 0
            try:
                cpu = p.cpu_percent(None)
            except psutil.AccessDenied:
                cpu = 0
            procs.append(
                {
                    "pid": info["pid"],
                    "name": info["name"] or "Unknown",
                    "cpu": cpu or 0,
                    "mem_mb": round(mem_mb, 1),
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if name:
        from thefuzz import fuzz

        procs = [p for p in procs if fuzz.partial_ratio(name.lower(), p["name"].lower()) > 60]
    sort_key = {
        "memory": lambda x: x["mem_mb"],
        "cpu": lambda x: x["cpu"],
        "name": lambda x: x["name"].lower(),
    }
    procs.sort(key=sort_key.get(sort_by, sort_key["memory"]), reverse=(sort_by != "name"))
    procs = procs[:limit]
    if not procs:
        return f"No processes found{f' matching {name}' if name else ''}."
    table = tabulate(
        [[p["pid"], p["name"], f"{p['cpu']:.1f}%", f"{p['mem_mb']:.1f} MB"] for p in procs],
        headers=["PID", "Name", "CPU%", "Memory"],
        tablefmt="simple",
    )
    return f"Processes ({len(procs)} shown):\n{table}"


def kill_process(
    name: str | None = None, pid: int | None = None, force: bool = False
) -> str:
    import psutil

    if pid is None and name is None:
        return "Error: Provide either pid or name parameter for kill mode."
    killed = []
    if pid is not None:
        try:
            p = psutil.Process(pid)
            pname = p.name()
            if force:
                p.kill()
            else:
                p.terminate()
            killed.append(f"{pname} (PID {pid})")
        except psutil.NoSuchProcess:
            return f"No process with PID {pid} found."
        except psutil.AccessDenied:
            return f"Access denied to kill PID {pid}. Try running as administrator."
    else:
        for p in psutil.process_iter(["pid", "name"]):
            try:
                if p.info["name"] and p.info["name"].lower() == name.lower():
                    if force:
                        p.kill()
                    else:
                        p.terminate()
                    killed.append(f"{p.info['name']} (PID {p.info['pid']})")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    if not killed:
        return f'No process matching "{name}" found or access denied.'
    return f"{'Force killed' if force else 'Terminated'}: {', '.join(killed)}"
