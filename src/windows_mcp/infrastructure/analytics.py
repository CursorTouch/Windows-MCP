from typing import Dict, Any, TypeVar, Callable, Protocol, Awaitable
from tempfile import TemporaryDirectory
from uuid_extensions import uuid7str
from fastmcp import Context
from functools import wraps
from pathlib import Path
import inspect
import posthog
import asyncio
import logging
import time
import os

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
formatter = logging.Formatter("[%(levelname)s] %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)

T = TypeVar("T")


class Analytics(Protocol):
    async def track_tool(self, tool_name: str, result: Dict[str, Any]) -> None:
        """Tracks the execution of a tool."""
        ...

    async def track_error(self, error: Exception, context: Dict[str, Any]) -> None:
        """Tracks an error that occurred during the execution of a tool."""
        ...

    async def is_feature_enabled(self, feature: str) -> bool:
        """Checks if a feature flag is enabled."""
        ...

    async def close(self) -> None:
        """Closes the analytics client."""
        ...


class PostHogAnalytics:
    TEMP_FOLDER = Path(TemporaryDirectory().name).parent
    API_KEY = os.environ.get(
        "POSTHOG_API_KEY",
        "phc_uxdCItyVTjXNU0sMPr97dq3tcz39scQNt3qjTYw5vLV",
    )
    HOST = os.environ.get("POSTHOG_HOST", "https://us.i.posthog.com")

    def __init__(self):
        self.client = None
        self._user_id = None
        self.mcp_interaction_id = f"mcp_{int(time.time() * 1000)}_{os.getpid()}"

        if not self.API_KEY:
            logger.warning("PostHog API key is empty; analytics client will not be initialized")
            return

        self.client = posthog.Posthog(
            self.API_KEY,
            host=self.HOST,
            disable_geoip=False,
            enable_exception_autocapture=True,
            debug=False,
        )

        if self.client:
            logger.debug(
                f"Initialized with user ID: {self.user_id} and session ID: {self.mcp_interaction_id}"
            )

    @property
    def user_id(self) -> str:
        if self._user_id:
            return self._user_id

        user_id_file = self.TEMP_FOLDER / ".windows-mcp-user-id"
        if user_id_file.exists():
            try:
                self._user_id = user_id_file.read_text(encoding="utf-8").strip()
            except OSError as e:
                logger.warning(f"Could not read persisted user ID: {e}")
                self._user_id = uuid7str()
                self._persist_user_id(user_id_file)
        else:
            self._user_id = uuid7str()
            self._persist_user_id(user_id_file)

        return self._user_id

    def _persist_user_id(self, user_id_file: Path) -> None:
        try:
            user_id_file.write_text(self._user_id, encoding="utf-8")
            try:
                user_id_file.chmod(0o600)
            except OSError as e:
                logger.debug(f"Could not restrict user ID file permissions: {e}")
        except Exception as e:
            logger.warning(f"Could not persist user ID: {e}")

    async def track_tool(self, tool_name: str, result: Dict[str, Any]) -> None:
        if self.client:
            self.client.capture(
                distinct_id=self.user_id,
                event="tool_executed",
                properties={
                    "tool_name": tool_name,
                    "session_id": self.mcp_interaction_id,
                    "process_person_profile": True,
                    **result,
                },
            )

        duration = result.get("duration_ms", 0)
        success_mark = "SUCCESS" if result.get("success") else "FAILED"
        # Using print for immediate visibility in console during debugging
        print(f"[Analytics] {tool_name}: {success_mark} ({duration}ms)")
        logger.info(f"{tool_name}: {success_mark} ({duration}ms)")

    async def track_error(self, error: Exception, context: Dict[str, Any]) -> None:
        if self.client:
            self.client.capture(
                distinct_id=self.user_id,
                event="exception",
                properties={
                    "exception": str(error),
                    "traceback": str(error) if not hasattr(error, "__traceback__") else str(error),
                    "session_id": self.mcp_interaction_id,
                    "process_person_profile": True,
                    **context,
                },
            )

        logger.error(f"ERROR in {context.get('tool_name')}: {error}")

    async def is_feature_enabled(self, feature: str) -> bool:
        if not self.client:
            return False
        return self.client.is_feature_enabled(feature, self.user_id)

    async def close(self) -> None:
        if self.client:
            self.client.shutdown()
            logger.debug("Closed analytics")


def with_analytics(analytics_instance: Analytics | None, tool_name: str):
    """
    Decorator to wrap tool functions with analytics tracking.
    """

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            start = time.time()

            # Capture client info from Context passed as argument
            client_data = {}
            try:
                ctx = next((arg for arg in args if isinstance(arg, Context)), None)
                if not ctx:
                    ctx = next(
                        (val for val in kwargs.values() if isinstance(val, Context)),
                        None,
                    )

                if (
                    ctx
                    and ctx.session
                    and ctx.session.client_params
                    and ctx.session.client_params.clientInfo
                ):
                    info = ctx.session.client_params.clientInfo
                    client_data["client_name"] = info.name
                    client_data["client_version"] = info.version
            except Exception:
                pass

            try:
                if inspect.iscoroutinefunction(func):
                    result = await func(*args, **kwargs)
                else:
                    # Run sync function in thread to avoid blocking loop
                    result = await asyncio.to_thread(func, *args, **kwargs)

                duration_ms = int((time.time() - start) * 1000)

                if analytics_instance:
                    await analytics_instance.track_tool(
                        tool_name,
                        {"duration_ms": duration_ms, "success": True, **client_data},
                    )

                return result
            except Exception as error:
                duration_ms = int((time.time() - start) * 1000)
                if analytics_instance:
                    await analytics_instance.track_error(
                        error,
                        {
                            "tool_name": tool_name,
                            "duration_ms": duration_ms,
                            **client_data,
                        },
                    )
                raise error

        return wrapper

    return decorator
