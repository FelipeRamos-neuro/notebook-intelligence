# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from ipykernel.kernelbase import Kernel

from notebook_intelligence.config import NBIConfig

from .backend import ChatbookBackend, load_kernel_specs, resolve_backend_kernel
from .codegen import (
    CHATBOOK_MSG_TYPE,
    ChatbookCodegenError,
    resolve_executable_source,
)
from .danger import merge_danger_scans, scan_generated_code
from .execution import (
    effective_execution_mode,
    should_execute_generated,
)
from .nbi_client import NBIClient, NBIClientError

log = logging.getLogger(__name__)


class ChatbookKernel(Kernel):
    implementation = "chatbook"
    implementation_version = "1.0"
    language = "chatbook"
    language_version = "1.0"
    language_info = {
        "name": "chatbook",
        "mimetype": "text/x-chatbook",
        "file_extension": ".chatbook",
        "pygments_lexer": "text",
    }
    banner = "Chatbook — natural-language cells, code via Notebook Intelligence"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._nbi = NBIClient()
        self._backend: Optional[ChatbookBackend] = None
        self._backend_info: dict[str, str] = {
            "name": "",
            "language": "python",
            "display_name": "",
        }
        self._interrupt = threading.Event()
        handlers = getattr(self, "shell_handlers", None)
        if isinstance(handlers, dict):
            for msg_type in ("comm_open", "comm_msg", "comm_close"):
                handlers[msg_type] = self._forward_comm

    def do_shutdown(self, restart):
        backend = self._backend
        self._backend = None
        if backend is not None:
            backend.shutdown()
        return super().do_shutdown(restart)

    def interrupt_request(self, stream, ident, parent):
        self._interrupt.set()
        try:
            self._nbi.cancel()
        except Exception:
            log.debug("Generate cancel failed", exc_info=True)
        if self._backend is not None:
            try:
                self._backend.interrupt()
            except Exception:
                log.debug("Backend interrupt failed", exc_info=True)
        self._abort_pending_executes(force=True)
        # The child interrupt above produces its own KeyboardInterrupt reply.
        # Calling ipykernel's implementation also SIGINTs this wrapper while
        # its shell thread is blocked relaying that reply, which can orphan the
        # active execute_request with no execute_reply.
        # jupyter_client only delivers this message when kernel.json sets
        # interrupt_mode to "message"; the default "signal" never calls us.
        self.session.send(
            stream, "interrupt_reply", {"status": "ok"}, parent, ident=ident
        )
        return None

    def complete_request(self, stream, ident, parent):
        return self._proxy_shell_request(stream, ident, parent, "complete_request")

    def inspect_request(self, stream, ident, parent):
        return self._proxy_shell_request(stream, ident, parent, "inspect_request")

    def comm_info_request(self, stream, ident, parent):
        return self._proxy_shell_request(stream, ident, parent, "comm_info_request")

    def is_complete_request(self, stream, ident, parent):
        return self._proxy_shell_request(stream, ident, parent, "is_complete_request")

    def _proxy_shell_request(self, stream, ident, parent, msg_type: str):
        reply_type = msg_type.replace("_request", "_reply")
        try:
            self._ensure_backend()
            content = self._backend.shell_request(
                msg_type, parent.get("content") or {}
            )
        except Exception as exc:
            content = {
                "status": "error",
                "ename": "ChatbookError",
                "evalue": str(exc),
            }
        self.session.send(stream, reply_type, content, parent, ident=ident)
        return None

    def _forward_comm(self, stream, ident, parent):
        msg_type = (parent.get("header") or {}).get("msg_type") or "comm_msg"
        try:
            self._ensure_backend()
            self._backend.forward_shell(msg_type, parent.get("content") or {})
        except Exception:
            log.debug("Failed to forward %s to backend", msg_type, exc_info=True)
        return None

    def execute_request(self, stream, ident, parent):
        self._interrupt.clear()
        content = dict(parent.get("content") or {})
        prompt = content.get("code") or ""
        metadata = parent.get("metadata") or {}
        chatbook_meta = metadata.get("nbi_chatbook") or {}
        cell_id = chatbook_meta.get("cellId") or metadata.get("cellId")

        try:
            self._ensure_backend()
        except Exception as exc:
            return self._reply_error(stream, ident, parent, str(exc))

        if is_code_execute(chatbook_meta):
            authored = chatbook_meta.get("codeSource")
            code = authored if isinstance(authored, str) and authored else prompt
            return self._execute_in_backend(stream, ident, parent, code)

        try:
            generated, info = resolve_executable_source(
                prompt, chatbook_meta, self._generate
            )
        except (ChatbookCodegenError, NBIClientError) as exc:
            if self._interrupt.is_set():
                return self._reply_interrupted(stream, ident, parent)
            return self._reply_error(stream, ident, parent, str(exc))

        if self._interrupt.is_set():
            return self._reply_interrupted(stream, ident, parent)

        language = self._backend_info.get("language") or ""
        scan = scan_generated_code(generated, language)
        if self._use_llm_danger_scan(chatbook_meta) and scan.get("level") != "risky":
            scan = merge_danger_scans(
                scan,
                self._llm_danger_scan(generated, chatbook_meta),
            )

        if self._interrupt.is_set():
            return self._reply_interrupted(stream, ident, parent)

        payload = {
            "cellId": cell_id,
            "generatedCode": generated,
            "promptHash": info.get("promptHash"),
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "cacheHit": bool(info.get("cacheHit")),
            "dangerLevel": scan.get("level"),
            "dangerReasons": list(scan.get("reasons") or []),
        }
        context_hash = chatbook_meta.get("contextHash")
        if context_hash:
            payload["contextHash"] = context_hash
        self._publish_chatbook_code(parent, payload)

        policy = self._execution_policy(chatbook_meta)
        if generated and should_execute_generated(policy, scan.get("level") or "risky"):
            if self._interrupt.is_set():
                return self._reply_interrupted(stream, ident, parent)
            return self._execute_in_backend(stream, ident, parent, generated)
        return self._reply_ok_without_execute(stream, ident, parent)

    def _reply_interrupted(self, stream, ident, parent):
        return self._reply_error(
            stream,
            ident,
            parent,
            "Interrupted",
            ename="KeyboardInterrupt",
        )

    def _execution_policy(self, chatbook_meta: dict) -> str:
        stored = ""
        try:
            stored = NBIConfig().chatbook_execution_mode
        except Exception:
            stored = ""
        return effective_execution_mode(
            chatbook_meta.get("executionPolicy"),
            stored or None,
        )

    def _use_llm_danger_scan(self, chatbook_meta: dict) -> bool:
        try:
            return bool(NBIConfig().chatbook_llm_danger_scan)
        except Exception:
            return bool(chatbook_meta.get("llmDangerScan"))

    def _ensure_backend(self) -> ChatbookBackend:
        if self._backend is not None and not self._backend.ready:
            try:
                self._backend.shutdown()
            except Exception:
                log.debug("Stale backend shutdown failed", exc_info=True)
            self._backend = None
        if self._backend is not None and self._backend.ready:
            return self._backend
        preferred = ""
        try:
            preferred = NBIConfig().chatbook_backend_kernel
        except Exception:
            preferred = ""
        info = resolve_backend_kernel(preferred, load_kernel_specs())
        backend = ChatbookBackend(info["name"], cwd=os.getcwd())
        try:
            backend.start()
        except Exception as exc:
            try:
                backend.shutdown()
            except Exception:
                pass
            raise RuntimeError(
                f"Could not start Chatbook backend kernel '{info['name']}'. "
                "Choose another kernelspec in Settings → Chatbook."
            ) from exc
        self._backend = backend
        self._backend_info = info
        return self._backend

    def _execute_in_backend(self, stream, ident, parent, code: str):
        try:
            reply = self._backend.execute(
                code,
                lambda msg_type, content: self.send_response(
                    self.iopub_socket, msg_type, content
                ),
            )
        except Exception as exc:
            return self._reply_error(stream, ident, parent, str(exc))
        # The child owns the prompt numbers the frontend already saw on its
        # relayed execute_input.
        count = reply.get("execution_count")
        if isinstance(count, int):
            self.execution_count = count
        else:
            self.execution_count += 1
        status = reply.get("status") or "ok"
        reply_content = {
            "status": status,
            "execution_count": self.execution_count,
            "user_expressions": reply.get("user_expressions") or {},
            "payload": reply.get("payload") or [],
        }
        if status == "error":
            reply_content["ename"] = reply.get("ename") or "ExecutionError"
            reply_content["evalue"] = reply.get("evalue") or ""
            reply_content["traceback"] = list(reply.get("traceback") or [])
        self.session.send(
            stream, "execute_reply", reply_content, parent, ident=ident
        )
        if status == "error":
            self._abort_pending_executes(parent)
        return None

    def _generate(self, prompt: str, chatbook_meta: dict) -> dict[str, Any]:
        context = chatbook_meta.get("notebookContext")
        rec = self._nbi.generate(
            prompt,
            notebook_context=context if isinstance(context, dict) else None,
            notebook_path=str(chatbook_meta.get("notebookPath") or ""),
            cell_id=str(chatbook_meta.get("cellId") or ""),
            prompt_hash=str(chatbook_meta.get("promptHash") or ""),
            context_hash=str(chatbook_meta.get("contextHash") or ""),
            language=str(self._backend_info.get("language") or "python"),
            kernel_name=str(self._backend_info.get("name") or ""),
            display_name=str(self._backend_info.get("display_name") or ""),
        )
        return {
            "generatedCode": rec.get("generatedCode") or rec.get("output") or ""
        }

    def _llm_danger_scan(self, code: str, chatbook_meta: dict) -> dict[str, Any]:
        try:
            return self._nbi.danger_scan(
                code,
                language=str(self._backend_info.get("language") or "python"),
            )
        except NBIClientError as exc:
            return {
                "level": "risky",
                "reasons": [f"Danger classifier failed: {exc}"],
            }

    def _publish_chatbook_code(self, parent, payload: dict) -> None:
        self.send_response(self.iopub_socket, CHATBOOK_MSG_TYPE, payload)

    def _reply_ok_without_execute(self, stream, ident, parent):
        """Complete execute_request without running generated code."""
        reply_content = {
            "status": "ok",
            "execution_count": self.execution_count,
            "user_expressions": {},
            "payload": [],
        }
        self.session.send(
            stream, "execute_reply", reply_content, parent, ident=ident
        )

    def _reply_error(
        self, stream, ident, parent, message: str, ename: str = "ChatbookError"
    ):
        traceback = [str(message)]
        self.send_response(
            self.iopub_socket,
            "error",
            {
                "ename": ename,
                "evalue": str(message),
                "traceback": traceback,
            },
        )
        self.session.send(
            stream,
            "execute_reply",
            {
                "status": "error",
                "ename": ename,
                "evalue": str(message),
                "traceback": traceback,
                "execution_count": self.execution_count,
                "user_expressions": {},
                "payload": [],
            },
            parent,
            ident=ident,
        )
        self._abort_pending_executes(parent)

    def _abort_pending_executes(self, parent: dict | None = None, force: bool = False) -> None:
        """Match ipykernel: abort queued execute_request after an error or interrupt."""
        if not force:
            content = (parent or {}).get("content") or {}
            if content.get("stop_on_error", True) is False:
                return
        abort = getattr(self, "_abort_queues", None)
        if not callable(abort):
            self._aborting = True
            return
        try:
            abort()
            return
        except TypeError:
            pass
        except Exception:
            log.debug("Could not abort execute queues", exc_info=True)
            return
        try:
            abort(None)
        except Exception:
            log.debug("Could not abort execute queues", exc_info=True)


def is_code_execute(chatbook_meta: dict | None) -> bool:
    return (chatbook_meta or {}).get("executeMode") == "code"
