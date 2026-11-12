"""Tests for UIA worker cancellation, occupancy telemetry, and stalled-lease release."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from fastmcp import Client, FastMCP

from windows_mcp.desktop.control import ControlCoordinator
from windows_mcp.infrastructure.thread_affinity import (
    UIAWorkerBusyError,
    run_uia_affine,
    uia_worker_status,
)
from windows_mcp.tools.control_notifications import ControlNotifier, ControlToolGate
from windows_mcp.tools.control_status import register as register_control_status


async def test_cancelled_uia_call_sets_abandoned_and_rejects_queueing():
    entered = threading.Event()
    release = threading.Event()
    try:

        def stalled():
            entered.set()
            assert release.wait(5)
            return "finished"

        first = asyncio.create_task(run_uia_affine(stalled))
        assert await asyncio.to_thread(entered.wait, 3)

        status_busy = uia_worker_status()
        assert status_busy["busy"] is True
        assert status_busy["abandoned_calls"] == 0

        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first

        status_abandoned = uia_worker_status()
        assert status_abandoned["busy"] is True
        assert status_abandoned["abandoned_calls"] == 1

        with pytest.raises(UIAWorkerBusyError, match="abandoned"):
            await run_uia_affine(lambda: "should-not-queue")
    finally:
        release.set()

    # Once the stalled worker finishes, normal UIA execution resumes cleanly.
    for _ in range(50):
        if not uia_worker_status()["busy"]:
            break
        await asyncio.sleep(0.01)

    status_idle = uia_worker_status()
    assert status_idle["busy"] is False
    assert status_idle["abandoned_calls"] == 0
    assert status_idle["last_completed_age_seconds"] is not None
    assert await run_uia_affine(lambda: "recovered") == "recovered"


async def test_queued_call_cancelled_before_start_is_skipped():
    entered = threading.Event()
    release = threading.Event()
    ran_second = False
    try:

        def first_job():
            entered.set()
            assert release.wait(5)
            return "first"

        def second_job():
            nonlocal ran_second
            ran_second = True
            return "second"

        first = asyncio.create_task(run_uia_affine(first_job))
        assert await asyncio.to_thread(entered.wait, 3)

        # Queues behind first_job because first_job has NOT been cancelled.
        second = asyncio.create_task(run_uia_affine(second_job))
        await asyncio.sleep(0.02)
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second

        release.set()
        assert await first == "first"
        # Wait briefly in case the worker drains the cancelled stub.
        assert await run_uia_affine(lambda: "ok") == "ok"
        assert ran_second is False
        assert uia_worker_status()["abandoned_calls"] == 0
    finally:
        release.set()


async def test_control_status_reports_ui_busy_and_releases_lease_on_busy_error(monkeypatch):
    owner = ControlCoordinator()
    owner._state = "ready"
    owner._suppress = True
    monkeypatch.setattr("windows_mcp.tools.control_status.get_controller", lambda: owner)

    mcp = FastMCP("worker-status-test")
    mcp.add_middleware(ControlToolGate(owner, ControlNotifier(owner)))
    register_control_status(mcp, get_desktop=lambda: None, get_analytics=lambda: None)

    entered = threading.Event()
    release = threading.Event()

    @mcp.tool(name="Click")
    async def hung_uia_tool() -> str:
        def work():
            entered.set()
            assert release.wait(5)
            return "done"

        return await run_uia_affine(work)

    try:
        async with Client(mcp) as client:
            idle_res = await client.call_tool("ControlStatus")
            assert idle_res.data["ui_busy"] is False
            assert idle_res.data["uia_worker"]["abandoned_calls"] == 0

            call_task = asyncio.create_task(client.call_tool("Click"))
            assert await asyncio.to_thread(entered.wait, 3)
            call_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await call_task

            busy_res = await asyncio.wait_for(client.call_tool("ControlStatus"), timeout=1.0)
            assert busy_res.data["ui_busy"] is True
            assert busy_res.data["uia_worker"]["busy"] is True
            assert busy_res.data["uia_worker"]["abandoned_calls"] == 1

            # Follow-up UIA tool fails fast and releases the held AI lease.
            followup = await client.call_tool("Click", raise_on_error=False)
            assert followup.is_error
            assert owner.status()["state"] == "ready"
            assert owner._suppress is False
    finally:
        release.set()
        for _ in range(50):
            if not uia_worker_status()["busy"]:
                break
            await asyncio.sleep(0.01)
        owner.stop()


def test_stalled_active_call_releases_ai_lease_after_timeout():
    owner = ControlCoordinator()
    owner._state = "ready"
    owner.call_lease_timeout_seconds = 0.05
    try:
        token = owner.begin_call("WaitFor")
        assert owner.status()["state"] == "ai"
        assert owner._suppress is True

        time.sleep(0.08)
        status = owner.status()
        assert status["state"] == "ready"
        assert owner._suppress is False

        owner.end_call(token)
    finally:
        owner.stop()
