"""Tests for util.terminate_process_tree.

These spawn real process trees and assert the whole tree is reaped, that a
SIGTERM-ignoring process is escalated to SIGKILL, and that the no-psutil
fallback still works. psutil is used by the tests themselves to inspect and
clean up processes (it is a declared test dependency).
"""

import os
import signal
import subprocess
import sys
import time

import psutil
import pytest

import notebook_intelligence.util as util

# A child that ignores SIGTERM, so only SIGKILL can stop it. It prints a line
# once the handler is installed so the test can wait for readiness rather than
# racing a fixed sleep.
IGNORE_TERM_SRC = (
    "import signal, time, sys\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "print('ready', flush=True)\n"
    "time.sleep(120)\n"
)

# A parent that spawns N long-sleeping children and then sleeps itself.
PARENT_SRC = (
    "import subprocess, sys, time\n"
    "kids = [subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])"
    " for _ in range({n})]\n"
    "time.sleep(120)\n"
)

# A three-level tree: parent -> child -> grandchild. Exercises the recursive
# descendant discovery (a grandchild a non-recursive walk would miss).
GRANDPARENT_SRC = (
    "import subprocess, sys, time\n"
    "subprocess.Popen([sys.executable, '-c',\n"
    "    'import subprocess, sys, time;'\n"
    "    'subprocess.Popen([sys.executable, \"-c\", \"import time; time.sleep(120)\"]);'\n"
    "    'time.sleep(120)'])\n"
    "time.sleep(120)\n"
)


def _alive(pid: int) -> bool:
    """True if pid exists and is not a reaped/zombie corpse."""
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def _assert_dead(pid: int, timeout: float = 3.0) -> None:
    """Poll until pid is gone/zombie. SIGKILL delivery is asynchronous, so a
    process can still read as RUNNING for a moment after the signal is sent."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _alive(pid):
            return
        time.sleep(0.05)
    assert not _alive(pid), f"pid {pid} survived teardown"


@pytest.fixture
def spawn():
    """Spawn-and-track helper that force-cleans any survivors after each test."""
    procs = []

    def _spawn(src, **kwargs):
        p = subprocess.Popen([sys.executable, "-c", src], **kwargs)
        procs.append(p)
        return p

    yield _spawn

    for p in procs:
        try:
            tree = psutil.Process(p.pid).children(recursive=True)
        except psutil.NoSuchProcess:
            tree = []
        for proc in tree:
            try:
                proc.kill()
            except psutil.Error:
                pass
        try:
            p.kill()
        except OSError:
            pass
        try:
            p.wait(timeout=2)
        except Exception:
            pass


def _wait_for_children(parent_pid, n, timeout=5.0):
    parent = psutil.Process(parent_pid)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        kids = parent.children(recursive=True)
        if len(kids) >= n:
            return kids
        time.sleep(0.05)
    return parent.children(recursive=True)


class TestTerminateProcessTree:
    def test_terminates_parent_and_all_descendants(self, spawn):
        p = spawn(PARENT_SRC.format(n=3))
        kids = _wait_for_children(p.pid, 3)
        assert len(kids) >= 3, "child processes did not start"
        pids = [p.pid] + [k.pid for k in kids]

        util.terminate_process_tree(p.pid, grace_seconds=3.0)

        for pid in pids:
            _assert_dead(pid)

    def test_terminates_deep_tree_including_grandchildren(self, spawn):
        p = spawn(GRANDPARENT_SRC)
        # parent -> child -> grandchild, so at least two descendants exist.
        descendants = _wait_for_children(p.pid, 2)
        assert len(descendants) >= 2, "deep tree did not start"
        # Capture every pid up front: once the parent dies the tree linkage is
        # gone, so an orphaned grandchild could not be rediscovered by walking.
        pids = [p.pid] + [d.pid for d in descendants]

        try:
            util.terminate_process_tree(p.pid, grace_seconds=3.0)
            for pid in pids:
                _assert_dead(pid)
        finally:
            # Guarantee no real process leaks even if an assertion above fails.
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass

    def test_escalates_to_sigkill_when_sigterm_ignored(self, spawn):
        p = spawn(IGNORE_TERM_SRC, stdout=subprocess.PIPE, text=True)
        assert p.stdout.readline().strip() == "ready"
        assert _alive(p.pid)

        start = time.monotonic()
        util.terminate_process_tree(p.pid, grace_seconds=1.0)
        elapsed = time.monotonic() - start

        _assert_dead(p.pid)
        # It must have waited out the grace window before forcing the kill,
        # proving the escalation path (not just a lucky SIGTERM) ran.
        assert elapsed >= 1.0

    def test_missing_pid_is_noop(self, spawn):
        p = spawn("pass")
        p.wait()
        # Already exited (and reaped); must not raise.
        util.terminate_process_tree(p.pid, grace_seconds=0.5)

    def test_none_and_invalid_pids_are_noops(self):
        util.terminate_process_tree(None)
        util.terminate_process_tree(0)
        util.terminate_process_tree(-5)


class TestNoPsutilFallback:
    def test_fallback_terminates_process(self, spawn, monkeypatch):
        monkeypatch.setattr(util, "psutil", None)
        p = spawn("import time; time.sleep(120)")
        time.sleep(0.2)
        assert _alive(p.pid)

        util.terminate_process_tree(p.pid, grace_seconds=1.0)

        _assert_dead(p.pid)

    def test_fallback_escalates_to_sigkill(self, spawn, monkeypatch):
        monkeypatch.setattr(util, "psutil", None)
        p = spawn(IGNORE_TERM_SRC, stdout=subprocess.PIPE, text=True)
        assert p.stdout.readline().strip() == "ready"

        util.terminate_process_tree(p.pid, grace_seconds=1.0)

        _assert_dead(p.pid)
