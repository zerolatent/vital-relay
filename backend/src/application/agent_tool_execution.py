"""Dedicated bounded execution capacity for private agent tool calls.

Agent-run endpoints are synchronous because a sandbox/model run can last for
many seconds.  Private tool calls must therefore not share Starlette's default
worker pool with those endpoints: enough concurrent runs could otherwise
occupy every worker while each run waits for a tool call that cannot start.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future as ConcurrentFuture
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from vital_relay.application.tool_proxy import (
    InternalAgentToolProxy,
    ToolProxyInvocation,
)


DEFAULT_AGENT_TOOL_WORKERS = 8
DEFAULT_AGENT_TOOL_PENDING = 16


class AgentToolExecutionSaturated(RuntimeError):
    """The bounded private tool executor has no remaining admission slots."""


class AgentToolExecutionPool:
    """Run host-authoritative tool calls outside the shared ASGI worker pool."""

    def __init__(
        self,
        *,
        max_workers: int = DEFAULT_AGENT_TOOL_WORKERS,
        max_pending: int = DEFAULT_AGENT_TOOL_PENDING,
    ) -> None:
        if max_workers < 1:
            raise ValueError("agent tool workers must be positive")
        if max_pending < max_workers:
            raise ValueError(
                "agent tool pending capacity cannot be smaller than workers"
            )
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="vital-relay-agent-tool",
        )
        self._admission = threading.BoundedSemaphore(max_pending)
        self._state_lock = threading.Lock()
        self._closed = False

    async def invoke(
        self,
        proxy: InternalAgentToolProxy,
        capability: str,
        invocation: ToolProxyInvocation,
    ) -> object:
        """Admit one call without creating an unbounded executor queue."""

        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[object] = loop.create_future()
        with self._state_lock:
            if self._closed:
                raise AgentToolExecutionSaturated(
                    "agent tool execution pool is closed"
                )
            if not self._admission.acquire(blocking=False):
                raise AgentToolExecutionSaturated(
                    "agent tool execution capacity is exhausted"
                )
            try:
                worker = self._executor.submit(
                    proxy.invoke,
                    capability,
                    invocation,
                )
                worker.add_done_callback(
                    lambda completed: self._complete_worker(
                        completed,
                        loop=loop,
                        waiter=waiter,
                    )
                )
            except BaseException:
                self._admission.release()
                raise
        # The worker Future is deliberately not chained to the HTTP waiter.
        # Cancelling a disconnected request cannot cancel queued authority work
        # or leave its exception unobserved.
        return await waiter

    def close(self) -> None:
        """Reject new work and join admitted calls during application shutdown."""

        with self._state_lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=False)

    def _complete_worker(
        self,
        completed: ConcurrentFuture[object],
        *,
        loop: asyncio.AbstractEventLoop,
        waiter: asyncio.Future[object],
    ) -> None:
        try:
            result = completed.result()
            error: Exception | None = None
        except Exception as exc:
            # Retrieving the exception here prevents the event loop from ever
            # logging raw provider/application diagnostics after disconnect.
            result = None
            error = exc
        except BaseException:
            result = None
            error = RuntimeError("agent tool worker terminated")
        finally:
            self._admission.release()
        try:
            loop.call_soon_threadsafe(
                _deliver_worker_outcome,
                waiter,
                result,
                error,
            )
        except RuntimeError:
            # Application shutdown may close the event loop after joining the
            # dedicated executor. The worker outcome has already been consumed.
            pass


def _deliver_worker_outcome(
    waiter: asyncio.Future[object],
    result: Any,
    error: Exception | None,
) -> None:
    if waiter.done():
        return
    if error is None:
        waiter.set_result(result)
    else:
        waiter.set_exception(error)
