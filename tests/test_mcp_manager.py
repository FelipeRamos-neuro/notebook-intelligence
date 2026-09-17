"""Regression tests for MCPServerImpl worker lifecycle behavior.

Mirrors TestWorkerThreadSignalRace in test_claude_client.py for the MCP code path,
confirming the snapshot pattern applied to mcp_manager._client_thread_func() is
equally locked in there.
"""

import asyncio
import logging
import threading
import time
from queue import Queue
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import mcp
import notebook_intelligence.mcp_manager as mcp_manager_module
import pytest
from mcp.types import ErrorData, METHOD_NOT_FOUND
from notebook_intelligence.api import SignalImpl
from notebook_intelligence.mcp_manager import (
    MCPServerEventType,
    MCPServerImpl,
    MCPServerStatus,
)


@pytest.fixture(autouse=True)
def _restore_stdio_log_filter():
    """connect() installs a filter on the process-global SDK logger.

    Left in place it silently drops records a later test asserts on, and it
    makes the install-once assertion depend on collection order.
    """
    logger = logging.getLogger("mcp.client.stdio")
    before = list(logger.filters)
    installed = mcp_manager_module._stdio_log_filter
    yield
    for flt in list(logger.filters):
        if flt not in before:
            logger.removeFilter(flt)
    mcp_manager_module._stdio_log_filter = installed


def _make_mcp_server():
    """Build an ``MCPServerImpl`` without invoking ``__init__`` / ``connect``."""
    server = MCPServerImpl.__new__(MCPServerImpl)
    server._manager = Mock(websocket_connector=None)
    server._name = "test"
    server._stdio_params = None
    server._streamable_http_params = None
    server._auto_approve_tools = set()
    server._tried_to_get_tool_list = False
    server._mcp_tools = []
    server._mcp_prompts = []
    server._session = None
    server._client_queue = Queue()
    server._client_thread_signal = SignalImpl()
    server._client_thread = None
    server._client_loop = None
    server._client_task = None
    server._client_cancel_requested = None
    server._client_handshake_done = None
    server._status = MCPServerStatus.NotConnected
    server._tool_prompt_list_lock = threading.Lock()
    server._connection_state_lock = threading.RLock()
    server._connection_generation = 1
    server._capability_retry_attempts = 0
    server._capability_retry_timer = None
    server._capability_retry_limit = 0
    return server


def _disconnect(server):
    """Simulate the field-nulling that disconnect() performs on the server instance."""
    server._client_queue = None
    server._client_thread_signal = None
    server._client_thread = None
    server._client_loop = None
    server._client_task = None
    server._client_cancel_requested = None
    server._client_handshake_done = None
    server._status = MCPServerStatus.NotConnected


class _SignalingQueue(Queue):
    def __init__(self):
        super().__init__()
        self.item_added = threading.Event()

    def put(self, item, block=True, timeout=None):
        super().put(item, block=block, timeout=timeout)
        self.item_added.set()


class TestMCPManagerWorkerThreadSignalRace:
    def test_snapshot_survives_disconnect(self):
        server = _make_mcp_server()
        original_signal = server._client_thread_signal
        received = []
        original_signal.connect(lambda data: received.append(data))
        signal = server._client_thread_signal
        _disconnect(server)
        assert server._client_thread_signal is None
        if signal is not None:
            signal.emit({"id": "x", "data": "stopped"})
        assert received == [{"id": "x", "data": "stopped"}]

    def test_signal_already_none_at_snapshot_time_is_safe(self):
        server = _make_mcp_server()
        _disconnect(server)
        signal = server._client_thread_signal
        assert signal is None
        if signal is not None:
            signal.emit({"id": "x", "data": "stopped"})

    def test_queue_snapshot_survives_disconnect(self):
        server = _make_mcp_server()
        original_queue = server._client_queue
        original_queue.put({"id": "x", "type": "list-tools"})
        queue = server._client_queue
        _disconnect(server)
        assert server._client_queue is None
        event = queue.get(block=False)
        assert event == {"id": "x", "type": "list-tools"}

    def test_queue_already_none_at_snapshot_time_exits_cleanly(self):
        server = _make_mcp_server()
        _disconnect(server)
        queue = server._client_queue
        assert queue is None
        if queue is None:
            return
        queue.get(block=False)


class _FailingPromptClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get_prompt(self, _name, _args):
        raise RuntimeError("prompt exploded")

    async def list_tools(self):
        return ["worker-survived"]


class _NonePayloadClient(_FailingPromptClient):
    async def list_tools(self):
        return None


class _ToolsOnlyClient(_FailingPromptClient):
    supports_tools = True
    supports_prompts = False

    async def list_prompts(self):
        raise mcp.McpError(ErrorData(
            code=METHOD_NOT_FOUND,
            message="Method not found",
        ))


class _BlockingToolsClient(_FailingPromptClient):
    def __init__(self, entered, release):
        self._entered = entered
        self._release = release

    async def list_tools(self):
        self._entered.set()
        while not self._release.is_set():
            await asyncio.sleep(0.01)
        return []


class _FailingEnterClient:
    def __init__(self, entered, fail):
        self._entered = entered
        self._fail = fail

    async def __aenter__(self):
        self._entered.set()
        while not self._fail.is_set():
            await asyncio.sleep(0.01)
        raise RuntimeError("client entry exploded")

    async def __aexit__(self, *_args):
        return None


class _HangingEnterClient:
    """A stdio command that never answers ``initialize``.

    Mirrors ``mcp_client.Client``: a failed startup unwinds its own transport
    stack, so ``__aexit__`` has nothing left to release. The recorded tasks
    pin the constraint that makes the unwind valid, namely that the anyio
    cancel scopes the SDK opens are exited by the task that entered them.
    """

    def __init__(self, entered, server=None, released=None):
        self._entered = entered
        self._server = server
        self._released = released or threading.Event()
        self.enter_task = None
        self.registered_task = None
        self.aexit_calls = 0

    async def __aenter__(self):
        self.enter_task = asyncio.current_task()
        # Read from the worker thread: the failure path clears this field, and
        # with a short deadline it can do so before the test looks.
        if self._server is not None:
            self.registered_task = self._server._client_task
        self._entered.set()
        while not self._released.is_set():
            await asyncio.sleep(0.01)

    async def __aexit__(self, *_args):
        self.aexit_calls += 1
        return None


def _run_worker(server, generation=1):
    """Start the worker the way connect() does and return its thread."""
    cancel_requested = threading.Event()
    handshake_done = threading.Event()
    server._client_cancel_requested = cancel_requested
    server._client_handshake_done = handshake_done
    worker = threading.Thread(
        target=asyncio.run,
        args=(server._client_thread_func(
            server._client_queue,
            server._client_thread_signal,
            generation,
            cancel_requested,
            handshake_done,
        ),),
        daemon=True,
    )
    server._client_thread = worker
    worker.start()
    return worker


def _disconnect_probe(stop_succeeds=False):
    """A connected server whose stop times out, or is acked when asked to.

    Returns the server plus the lists recording the timeout each StopServer
    was sent with and the generations the cancel escalation was asked for.
    """
    server = _make_mcp_server()
    server._client_thread = threading.current_thread()
    server._client_handshake_done = threading.Event()
    timeouts = []
    cancelled = []
    server._cancel_client_worker = lambda generation: cancelled.append(generation)

    def _send(event_type, _event_args=None, timeout=None):
        timeouts.append(timeout)
        if stop_succeeds:
            return {"success": True, "error": None, "data": "stopped"}
        return {"success": False, "error": "response timeout", "data": None}

    server._send_mcp_request = _send
    return server, timeouts, cancelled


def _wait_for_registered_task(server, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = server._client_task
        if task is not None:
            return task
        time.sleep(0.01)
    return None


class TestMCPManagerStuckHandshake:
    """A command that is not an MCP server must not hold the worker.

    Unwinding the client context is what releases its stdio subprocess, so
    these pin the unwind; the process itself is the SDK's to reap.
    """

    def test_handshake_timeout_fails_the_server_and_unwinds_the_client(
        self,
        monkeypatch,
        caplog,
    ):
        monkeypatch.setattr(mcp_manager_module, "MCP_CONNECT_TIMEOUT", 0.05)
        server = _make_mcp_server()
        entered = threading.Event()
        client = _HangingEnterClient(entered, server=server)

        async def _get_client():
            return client

        server._get_client = _get_client
        worker = _run_worker(server)

        assert entered.wait(timeout=1)
        worker.join(timeout=2)
        assert not worker.is_alive()
        assert server.status == MCPServerStatus.FailedToConnect
        assert server._client_thread is None
        assert "did not complete the MCP handshake" in caplog.text
        # The deadline must cancel the worker's own task rather than a child
        # of it: anyio refuses to exit a cancel scope from another task, so a
        # cross-task deadline would break every teardown on Python < 3.12.
        assert client.registered_task is not None
        assert client.enter_task is client.registered_task
        # A client that failed to start has already unwound itself, so the
        # worker must not enter its context a second time.
        assert client.aexit_calls == 0

    def test_disconnect_cancels_a_worker_it_cannot_reach(
        self,
        monkeypatch,
        caplog,
    ):
        caplog.set_level(logging.DEBUG, logger=mcp_manager_module.log.name)
        monkeypatch.setattr(mcp_manager_module, "MCP_CONNECT_TIMEOUT", 30)
        monkeypatch.setattr(mcp_manager_module, "MCP_DISCONNECT_TIMEOUT", 0.05)
        # Without this a regression in the stop timeout would hit the file's
        # 30s pytest-timeout cap and report as a kill, not as a failure.
        monkeypatch.setattr(
            mcp_manager_module, "MCP_SERVER_RESPONSE_TIMEOUT", 0.2
        )
        server = _make_mcp_server()
        entered = threading.Event()
        client = _HangingEnterClient(entered, server=server)

        async def _get_client():
            return client

        server._get_client = _get_client
        worker = _run_worker(server)
        assert entered.wait(timeout=1)
        # The worker is parked in the handshake, so StopServer can never be
        # acked; without the cancel fallback this leaves the process running.
        assert _wait_for_registered_task(server) is not None

        server.disconnect()

        worker.join(timeout=2)
        assert not worker.is_alive()
        assert server.status == MCPServerStatus.NotConnected
        assert server._client_thread is None
        assert server._client_task is None
        # A teardown we asked for is not a crash. Reporting it as one would
        # broadcast FailedToConnect over a connection the caller just closed.
        assert "Error occurred while running MCP server thread" not in caplog.text
        assert "stopped on request" in caplog.text

    # A healthy server does not sit at Connected: connect() starts the
    # capability refresh immediately, which walks the status onwards. Whatever
    # it currently reads, a completed handshake must keep the full window.
    @pytest.mark.parametrize(
        "status",
        [
            MCPServerStatus.Connected,
            MCPServerStatus.UpdatingToolList,
            MCPServerStatus.UpdatedPromptList,
            MCPServerStatus.FailedToUpdateToolList,
        ],
    )
    def test_stop_after_a_completed_handshake_keeps_the_full_timeout(self, status):
        """A slow tool call must keep its full window on an ordinary disconnect.

        Only a worker still inside the handshake gets the short window, since
        it is the only one that cannot read StopServer at all. Whatever the
        status reads, a completed handshake waits out the request timeout.
        """
        server, timeouts, cancelled = _disconnect_probe()
        server._status = status
        server._client_handshake_done.set()
        generation = server._connection_generation

        server.disconnect()

        # None means "use MCP_SERVER_RESPONSE_TIMEOUT", the full window.
        assert timeouts == [None]
        # That window has now elapsed unacked, so there is no work left to
        # protect. Skipping the escalation here strands the worker and its
        # subprocess with no handle left to reach them by.
        assert cancelled == [generation]

    def test_stop_that_is_acked_leaves_the_worker_alone(self):
        server, timeouts, cancelled = _disconnect_probe(stop_succeeds=True)
        server._client_handshake_done.set()

        server.disconnect()

        assert timeouts == [None]
        assert cancelled == []

    def test_stop_during_the_handshake_is_bounded_and_escalates(self):
        server, timeouts, cancelled = _disconnect_probe()
        generation = server._connection_generation

        server.disconnect()

        assert timeouts == [mcp_manager_module.MCP_DISCONNECT_TIMEOUT]
        assert cancelled == [generation]

    def test_cancel_ignores_a_generation_the_connection_moved_past(self):
        """A slow disconnect must not cancel the connection that replaced it."""
        server = _make_mcp_server()
        server._client_loop = Mock()
        server._client_task = Mock()
        server._client_cancel_requested = threading.Event()

        server._cancel_client_worker(server._connection_generation - 1)

        assert not server._client_cancel_requested.is_set()
        server._client_loop.call_soon_threadsafe.assert_not_called()

    def test_worker_failure_reports_the_stdio_output_it_withheld(self, caplog):
        """The suppressed count has to reach the log the operator reads.

        A burst that ends when its server dies never rolls the filter's
        window, so without this the tail is counted and never mentioned.
        """
        server = _make_mcp_server()
        entered = threading.Event()
        fail = threading.Event()
        client = _FailingEnterClient(entered, fail)

        async def _get_client():
            return client

        server._get_client = _get_client
        filter_ = mcp_manager_module._BurstLimitingFilter(burst=1, interval=600)
        for _ in range(4):
            filter_.filter(
                logging.LogRecord(
                    "mcp.client.stdio", logging.ERROR, __file__, 1, "boom", None, None
                )
            )
        mcp_manager_module._stdio_log_filter = filter_

        worker = _run_worker(server)
        assert entered.wait(timeout=1)
        fail.set()
        worker.join(timeout=2)

        assert not worker.is_alive()
        assert "3 unparseable output message(s) that were not logged" in caplog.text


class TestStdioLogBurstLimit:
    def test_burst_is_capped_and_reports_what_it_dropped(self):
        f = mcp_manager_module._BurstLimitingFilter(burst=2, interval=60)
        records = [
            logging.LogRecord("mcp.client.stdio", logging.ERROR, __file__, 1, "boom", None, None)
            for _ in range(5)
        ]
        assert [f.filter(r) for r in records] == [True, True, False, False, False]

        f._window_start -= 120
        rolled = logging.LogRecord(
            "mcp.client.stdio", logging.ERROR, __file__, 1, "boom", None, None
        )
        assert f.filter(rolled) is True
        assert "3 similar messages suppressed" in rolled.msg

    def test_rolled_window_keeps_the_arguments_of_the_record_it_annotates(self):
        f = mcp_manager_module._BurstLimitingFilter(burst=1, interval=60)
        first = logging.LogRecord(
            "mcp.client.stdio", logging.ERROR, __file__, 1, "server %s died", ("foo",), None
        )
        assert f.filter(first) is True
        assert f.filter(first) is False

        f._window_start -= 120
        rolled = logging.LogRecord(
            "mcp.client.stdio", logging.ERROR, __file__, 1, "server %s died", ("foo",), None
        )
        assert f.filter(rolled) is True
        # Appending to an unformatted msg and dropping args would leave the
        # placeholder behind and lose the value it stood for.
        assert rolled.getMessage() == "server foo died (1 similar message suppressed)"

    def test_unreported_drops_can_be_drained_for_a_failure_message(self):
        """A burst that ends when its server is killed never rolls its window,
        so the tail has to be collectable by the code reporting the failure."""
        f = mcp_manager_module._BurstLimitingFilter(burst=1, interval=60)
        for _ in range(4):
            f.filter(
                logging.LogRecord(
                    "mcp.client.stdio", logging.ERROR, __file__, 1, "boom", None, None
                )
            )
        assert f.drain_dropped() == 3
        assert f.drain_dropped() == 0

    def test_filter_is_installed_once(self):
        logger = logging.getLogger("mcp.client.stdio")
        before = list(logger.filters)
        installed_before = mcp_manager_module._stdio_log_filter
        try:
            mcp_manager_module._stdio_log_filter = None
            mcp_manager_module._install_stdio_log_burst_limit()
            mcp_manager_module._install_stdio_log_burst_limit()
            installed = [
                flt for flt in logger.filters
                if isinstance(flt, mcp_manager_module._BurstLimitingFilter)
                and flt not in before
            ]
            assert len(installed) == 1
            assert installed[0] is mcp_manager_module._stdio_log_filter
            assert mcp_manager_module._drain_stdio_log_drops() == 0
        finally:
            # This logger is process-global: a filter left behind would drop
            # records other tests assert on.
            for flt in list(logger.filters):
                if flt not in before:
                    logger.removeFilter(flt)
            mcp_manager_module._stdio_log_filter = installed_before


class TestMCPManagerEventFailures:
    def test_timed_out_disconnect_keeps_stale_worker_on_old_queue(
        self,
        monkeypatch,
    ):
        monkeypatch.setattr(
            mcp_manager_module,
            "MCP_SERVER_RESPONSE_TIMEOUT",
            0.01,
        )
        server = _make_mcp_server()
        old_queue = server._client_queue
        old_signal = server._client_thread_signal
        entered = threading.Event()
        release = threading.Event()
        client = _BlockingToolsClient(entered, release)

        async def _get_client():
            return client

        server._get_client = _get_client
        old_worker = _run_worker(server)

        request_result = {}
        sender = threading.Thread(
            target=lambda: request_result.update(
                response=server._send_mcp_request(MCPServerEventType.ListTools)
            ),
            daemon=True,
        )
        sender.start()
        assert entered.wait(timeout=1)
        # The worker publishes handshake completion; disconnect() reads it to
        # decide this stop keeps the full response timeout.
        assert server._client_handshake_done.is_set()

        # StopServer queues behind the blocked tools call and times out. The
        # completed disconnect invalidates generation 1 without joining it.
        server.disconnect()

        # Simulate connect() installing generation 3 while the old client call
        # is still in flight. The stale worker must retain old_queue rather
        # than rereading these replacement fields on its next loop.
        new_queue = Queue()
        replacement_event = {
            "id": "new-generation",
            "type": MCPServerEventType.ListTools,
            "args": None,
        }
        with server._connection_state_lock:
            server._connection_generation += 1
            server._client_queue = new_queue
            server._client_thread_signal = SignalImpl()
            server._client_thread = Mock()
            server._status = MCPServerStatus.Connecting
        new_queue.put(replacement_event)

        release.set()
        old_worker.join(timeout=1)
        sender.join(timeout=1)

        assert new_queue.get(block=False) == replacement_event
        assert server.status == MCPServerStatus.Connecting
        assert "response" in request_result
        assert not old_worker.is_alive()
        assert not sender.is_alive()

    def test_unadvertised_prompt_capability_is_logged_and_returns_empty(
        self,
        caplog,
    ):
        caplog.set_level("DEBUG", logger="notebook_intelligence.mcp_manager")
        server = _make_mcp_server()
        client = _ToolsOnlyClient()

        async def _get_client():
            return client

        server._get_client = _get_client
        worker = threading.Thread(
            target=asyncio.run,
            args=(server._client_thread_func(),),
            daemon=True,
        )
        server._client_thread = worker
        worker.start()

        response = server._send_mcp_request(MCPServerEventType.ListPrompts)
        server._send_mcp_request(MCPServerEventType.StopServer)
        worker.join(timeout=2)

        assert response == {"data": [], "success": True, "error": None}
        assert "did not advertise prompts" in caplog.text
        assert not worker.is_alive()

    def test_prompt_failure_returns_immediately_and_worker_stays_alive(self):
        server = _make_mcp_server()
        client = _FailingPromptClient()

        async def _get_client():
            return client

        server._get_client = _get_client
        worker = threading.Thread(
            target=asyncio.run,
            args=(server._client_thread_func(),),
            daemon=True,
        )
        server._client_thread = worker
        worker.start()

        failed = server._send_mcp_request(
            MCPServerEventType.GetPromptValue,
            {"prompt_name": "broken", "prompt_args": {}},
        )
        survived = server._send_mcp_request(MCPServerEventType.ListTools)
        stopped = server._send_mcp_request(MCPServerEventType.StopServer)
        worker.join(timeout=2)

        assert failed["success"] is False
        assert "prompt exploded" in failed["error"]
        assert survived == {
            "data": ["worker-survived"],
            "success": True,
            "error": None,
        }
        assert stopped["success"] is True
        assert not worker.is_alive()

    def test_successful_none_payload_returns_as_terminal_response(self):
        server = _make_mcp_server()
        client = _NonePayloadClient()

        async def _get_client():
            return client

        server._get_client = _get_client
        worker = threading.Thread(
            target=asyncio.run,
            args=(server._client_thread_func(),),
            daemon=True,
        )
        server._client_thread = worker
        worker.start()

        response = server._send_mcp_request(MCPServerEventType.ListTools)
        server._send_mcp_request(MCPServerEventType.StopServer)
        worker.join(timeout=2)

        assert response == {
            "data": None,
            "success": True,
            "error": None,
        }
        assert not worker.is_alive()

    def test_unknown_event_returns_error_instead_of_timing_out(self):
        server = _make_mcp_server()
        client = _FailingPromptClient()

        async def _get_client():
            return client

        server._get_client = _get_client
        worker = threading.Thread(
            target=asyncio.run,
            args=(server._client_thread_func(),),
            daemon=True,
        )
        server._client_thread = worker
        worker.start()

        response = server._send_mcp_request("unknown-event")
        server._send_mcp_request(MCPServerEventType.StopServer)
        worker.join(timeout=2)

        assert response["success"] is False
        assert "Unknown MCP server event type" in response["error"]
        assert not worker.is_alive()

    def test_in_flight_request_stops_when_worker_exits(self):
        server = _make_mcp_server()
        queue = _SignalingQueue()
        server._client_queue = queue
        entered = threading.Event()
        fail = threading.Event()
        client = _FailingEnterClient(entered, fail)

        async def _get_client():
            return client

        server._get_client = _get_client
        worker = threading.Thread(
            target=asyncio.run,
            args=(server._client_thread_func(),),
            daemon=True,
        )
        server._client_thread = worker
        worker.start()
        assert entered.wait(timeout=1)

        result = {}
        sender = threading.Thread(
            target=lambda: result.update(
                response=server._send_mcp_request(MCPServerEventType.ListTools)
            ),
            daemon=True,
        )
        sender.start()
        assert queue.item_added.wait(timeout=1)
        fail.set()
        sender.join(timeout=1)
        worker.join(timeout=1)

        response = result["response"]
        assert response["success"] is False
        assert "worker stopped" in response["error"]
        assert not sender.is_alive()
        assert not worker.is_alive()

    def test_initial_refresh_preserves_worker_failure_status(self):
        server = _make_mcp_server()
        queue = _SignalingQueue()
        server._client_queue = queue
        server._mcp_tools = [Mock()]
        server._mcp_prompts = [Mock()]
        retry_timer = Mock()
        server._capability_retry_timer = retry_timer
        server._capability_retry_attempts = 3
        entered = threading.Event()
        fail = threading.Event()
        client = _FailingEnterClient(entered, fail)

        async def _get_client():
            return client

        server._get_client = _get_client
        worker = threading.Thread(
            target=asyncio.run,
            args=(server._client_thread_func(),),
            daemon=True,
        )
        server._client_thread = worker
        worker.start()
        assert entered.wait(timeout=1)

        refresh = threading.Thread(
            target=server._update_tool_and_prompt_list,
            daemon=True,
        )
        refresh.start()
        assert queue.item_added.wait(timeout=1)
        fail.set()
        refresh.join(timeout=1)
        worker.join(timeout=1)

        assert server.status == MCPServerStatus.FailedToConnect
        assert server.get_tools() == []
        assert server.get_prompts() == []
        retry_timer.cancel.assert_called_once_with()
        assert server._capability_retry_attempts == 0
        assert not refresh.is_alive()
        assert not worker.is_alive()


class TestMCPManagerCapabilityRefresh:
    @pytest.mark.parametrize("value", ["invalid", "0", "-1", "nan", "inf"])
    def test_invalid_timing_environment_values_use_default(
        self,
        monkeypatch,
        value,
    ):
        monkeypatch.setenv("NBI_TEST_TIMING", value)

        assert mcp_manager_module._read_float_env("NBI_TEST_TIMING", 5) == 5

    def test_concurrent_connect_starts_only_one_worker(self, monkeypatch):
        server = _make_mcp_server()
        server._client_thread = None
        worker = Mock()
        thread_factory = Mock(return_value=worker)
        real_thread = threading.Thread
        monkeypatch.setattr(mcp_manager_module.threading, "Thread", thread_factory)
        server._update_tool_and_prompt_list_async = Mock()

        callers = [real_thread(target=server.connect) for _ in range(2)]
        for caller in callers:
            caller.start()
        for caller in callers:
            caller.join(timeout=1)

        worker_coroutine = thread_factory.call_args.kwargs["args"][0]
        worker_coroutine.close()
        assert thread_factory.call_count == 1
        worker.start.assert_called_once_with()
        server._update_tool_and_prompt_list_async.assert_called_once_with(2)

    def test_worker_start_failure_clears_aborted_connection_resources(
        self,
        monkeypatch,
    ):
        server = _make_mcp_server()
        server._client_thread = None
        worker = Mock()
        worker.start.side_effect = RuntimeError("worker unavailable")
        thread_factory = Mock(return_value=worker)
        monkeypatch.setattr(mcp_manager_module.threading, "Thread", thread_factory)

        server.connect()
        worker_coroutine = thread_factory.call_args.kwargs["args"][0]
        worker_coroutine.close()

        assert server.status == MCPServerStatus.FailedToConnect
        assert server._client_thread is None
        assert server._client_queue is None
        assert server._client_thread_signal is None

    def test_each_worker_gets_a_fresh_client(self):
        server = _make_mcp_server()
        server._stdio_params = Mock()
        first_client = Mock()
        second_client = Mock()
        server._create_client = Mock(side_effect=[first_client, second_client])

        first = asyncio.run(server._get_client())
        second = asyncio.run(server._get_client())

        assert first is first_client
        assert second is second_client
        assert first is not second

    def test_request_state_snapshot_is_taken_under_connection_lock(self):
        server = _make_mcp_server()
        worker = Mock()
        worker.is_alive.return_value = False
        server._client_thread = worker
        lock = MagicMock()
        server._connection_state_lock = lock

        response = server._send_mcp_request(MCPServerEventType.ListTools)

        lock.__enter__.assert_called_once_with()
        lock.__exit__.assert_called_once()
        assert response["success"] is False

    def test_refresh_thread_start_failure_stops_started_worker(self, monkeypatch):
        server = _make_mcp_server()
        server._client_thread = None
        stale_retry_timer = Mock()
        server._capability_retry_timer = stale_retry_timer
        server._capability_retry_attempts = 3
        worker = Mock()
        thread_factory = Mock(return_value=worker)
        monkeypatch.setattr(mcp_manager_module.threading, "Thread", thread_factory)
        server._update_tool_and_prompt_list_async = Mock(
            side_effect=RuntimeError("refresh thread unavailable")
        )
        server._send_mcp_request = Mock(return_value={
            "data": "stopped",
            "success": True,
            "error": None,
        })

        server.connect()
        worker_coroutine = thread_factory.call_args.kwargs["args"][0]
        worker_coroutine.close()

        worker.start.assert_called_once_with()
        stale_retry_timer.cancel.assert_called_once_with()
        assert server._capability_retry_attempts == 0
        # The server never connected, so its stop gets the same bounded window
        # the disconnect path uses instead of waiting out a request timeout.
        server._send_mcp_request.assert_called_once_with(
            MCPServerEventType.StopServer,
            timeout=mcp_manager_module.MCP_DISCONNECT_TIMEOUT,
        )
        assert server.status == MCPServerStatus.FailedToConnect
        assert server._client_thread is None
        assert server._client_queue is None

    def test_refresh_failure_escalates_to_a_cancel_when_the_stop_is_not_acked(
        self,
        monkeypatch,
    ):
        """connect()'s own failure path owes the same escalation as disconnect.

        The worker it started may be parked in the handshake, where StopServer
        can never be read, so without the cancel it keeps its subprocess.
        """
        server = _make_mcp_server()
        server._client_thread = None
        worker = Mock()
        thread_factory = Mock(return_value=worker)
        monkeypatch.setattr(
            mcp_manager_module.threading, "Thread", thread_factory
        )
        server._update_tool_and_prompt_list_async = Mock(
            side_effect=RuntimeError("refresh thread unavailable")
        )
        server._send_mcp_request = Mock(return_value={
            "data": None,
            "success": False,
            "error": "response timeout",
        })
        cancelled = []
        server._cancel_client_worker = lambda generation: cancelled.append(
            generation
        )
        generation_before = server._connection_generation

        server.connect()
        thread_factory.call_args.kwargs["args"][0].close()

        assert cancelled == [generation_before + 1]
        assert server.status == MCPServerStatus.FailedToConnect

    def test_transient_direct_refresh_schedules_retry(self):
        server = _make_mcp_server()
        server._client_thread = Mock()
        server._capability_retry_limit = 1
        server._send_mcp_request = Mock(return_value={
            "data": None,
            "success": False,
            "error": "temporary timeout",
        })
        server._schedule_capability_refresh_retry = Mock()

        assert server.update_tool_list() is False

        server._schedule_capability_refresh_retry.assert_called_once_with(1)

    def test_capability_retry_is_bounded_and_runs_for_current_generation(
        self,
        monkeypatch,
    ):
        monkeypatch.setattr(
            mcp_manager_module,
            "MCP_CAPABILITY_RETRY_DELAY",
            0.05,
        )
        server = _make_mcp_server()
        server._client_thread = Mock()
        server._capability_retry_limit = 1
        server._update_tool_and_prompt_list = Mock()

        server._schedule_capability_refresh_retry(1)
        timer = server._capability_retry_timer
        timer.join(timeout=1)
        server._schedule_capability_refresh_retry(1)

        server._update_tool_and_prompt_list.assert_called_once_with(1)
        assert server._capability_retry_attempts == 1
        assert not timer.is_alive()

    def test_disconnect_invalidates_in_flight_refresh_status(self):
        server = _make_mcp_server()
        server._client_thread = Mock()
        refresh_started = threading.Event()
        release_refresh = threading.Event()

        def _send(event_type, _event_args=None, timeout=None):
            if event_type == MCPServerEventType.StopServer:
                return {"data": "stopped", "success": True, "error": None}
            assert event_type == MCPServerEventType.ListTools
            refresh_started.set()
            assert release_refresh.wait(timeout=1)
            return {"data": [], "success": True, "error": None}

        server._send_mcp_request = Mock(side_effect=_send)
        refresh = threading.Thread(
            target=server._update_tool_and_prompt_list,
            daemon=True,
        )
        refresh.start()
        assert refresh_started.wait(timeout=1)

        server.disconnect()
        release_refresh.set()
        refresh.join(timeout=1)

        assert server.status == MCPServerStatus.NotConnected
        assert not refresh.is_alive()

    def test_delayed_refresh_aborts_after_disconnect(self):
        server = _make_mcp_server()
        server._client_thread = Mock()
        server._status = MCPServerStatus.Connected
        server._send_mcp_request = Mock(return_value={
            "data": "stopped",
            "success": True,
            "error": None,
        })

        server.disconnect()
        server._send_mcp_request.reset_mock()
        server._update_tool_and_prompt_list(expected_generation=1)

        assert server.status == MCPServerStatus.NotConnected
        server._send_mcp_request.assert_not_called()

    def test_tool_failure_does_not_suppress_successful_prompt_refresh(self):
        server = _make_mcp_server()
        server._client_thread = Mock()
        prompt = SimpleNamespace(
            name="healthy-prompt",
            title="Healthy prompt",
            description="still available",
            arguments=[],
        )
        server._send_mcp_request = Mock(side_effect=[
            {"data": None, "success": False, "error": "tools failed"},
            {"data": [prompt], "success": True, "error": None},
        ])

        server._update_tool_and_prompt_list()

        assert server.get_tools() == []
        assert [item.name for item in server.get_prompts()] == ["healthy-prompt"]
        assert server.status == MCPServerStatus.FailedToUpdateToolList
        assert [item.args for item in server._send_mcp_request.call_args_list] == [
            (MCPServerEventType.ListTools,),
            (MCPServerEventType.ListPrompts,),
        ]

    def test_failed_refreshes_preserve_last_known_tools_and_prompts(self):
        server = _make_mcp_server()
        server._client_thread = Mock()
        previous_tools = [Mock()]
        previous_prompts = [Mock()]
        server._mcp_tools = previous_tools
        server._mcp_prompts = previous_prompts
        server._send_mcp_request = Mock(side_effect=[
            {"data": None, "success": False, "error": "tools failed"},
            {"data": None, "success": False, "error": "prompts failed"},
        ])

        server.update_tool_list()
        server.update_prompts_list()

        assert server._mcp_tools is previous_tools
        assert server._mcp_prompts is previous_prompts
        assert server.status == MCPServerStatus.FailedToUpdatePromptList

    def test_disconnected_direct_refresh_preserves_cached_capabilities(self):
        server = _make_mcp_server()
        previous_tools = [Mock()]
        previous_prompts = [Mock()]
        server._mcp_tools = previous_tools
        server._mcp_prompts = previous_prompts

        assert server.update_tool_list() is False
        assert server.update_prompts_list() is False

        assert server._mcp_tools is previous_tools
        assert server._mcp_prompts is previous_prompts

    def test_invalid_list_payloads_preserve_last_known_collections(self):
        server = _make_mcp_server()
        server._client_thread = Mock()
        previous_tools = [Mock()]
        previous_prompts = [Mock()]
        server._mcp_tools = previous_tools
        server._mcp_prompts = previous_prompts
        server._send_mcp_request = Mock(side_effect=[
            {"data": None, "success": True, "error": None},
            {"data": None, "success": True, "error": None},
        ])

        server.update_tool_list()
        server.update_prompts_list()

        assert server._mcp_tools is previous_tools
        assert server._mcp_prompts is previous_prompts
        assert server.status == MCPServerStatus.FailedToUpdatePromptList

    def test_invalid_prompt_messages_return_none(self):
        server = _make_mcp_server()
        prompt = Mock(arguments=[])
        server.get_prompt = Mock(return_value=prompt)
        server._send_mcp_request = Mock(return_value={
            "data": None,
            "success": True,
            "error": None,
        })

        result = server.get_prompt_value("broken")

        assert result is None
