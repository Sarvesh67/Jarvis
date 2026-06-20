"""Minimal ACP (Agent Client Protocol) client for driving Hermes as a live agent.

Hermes speaks ACP over stdio as newline-delimited JSON-RPC 2.0 (the same protocol
Zed/VS Code use). We spawn `hermes -p <alias> [-m model] [-t toolsets] [--yolo] acp`,
do the initialize -> session/new handshake, then stream `session/update` notifications
(tool_call, tool_call_update, agent_message_chunk, agent_thought_chunk, plan,
usage_update) back to the browser and round-trip `session/request_permission`.

One persistent agent process per project alias, kept alive across turns so the
conversation has memory. Changing model/toolsets restarts the process and resumes
the same session (session/load) so context survives.
"""
import asyncio
import json
import os
import pathlib

HERMES = str(pathlib.Path.home() / ".local" / "bin" / "hermes")
HOME = str(pathlib.Path.home())
PIPE = asyncio.subprocess.PIPE


def _env() -> dict:
    return {
        **os.environ,
        "HOME": HOME,
        "PATH": f"{HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:"
        + os.environ.get("PATH", ""),
    }


class AcpError(Exception):
    pass


class AcpAgent:
    """A live Hermes ACP session for one project alias."""

    def __init__(self, alias: str):
        self.alias = alias
        self.proc = None
        self.session_id = None
        self.cfg = None  # (model, toolsets tuple, yolo)
        self._idc = 0
        self._pending = {}  # request id -> Future
        self._reader = None
        self._update_cb = None
        self._perm_cb = None
        self._alive = False
        self._starting = asyncio.Lock()

    def _nid(self) -> int:
        self._idc += 1
        return self._idc

    async def _send(self, method: str, params: dict, is_req: bool = True):
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        fut = None
        if is_req:
            i = self._nid()
            msg["id"] = i
            fut = asyncio.get_event_loop().create_future()
            self._pending[i] = fut
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())
        await self.proc.stdin.drain()
        return fut

    async def _respond(self, req_id, result: dict):
        self.proc.stdin.write(
            (json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}) + "\n").encode()
        )
        await self.proc.stdin.drain()

    async def _read_loop(self):
        try:
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    break
                line = line.decode(errors="replace").strip()
                if not line:
                    continue
                try:
                    m = json.loads(line)
                except Exception:
                    continue
                if "method" in m:
                    await self._on_incoming(m)
                elif "id" in m:
                    fut = self._pending.pop(m["id"], None)
                    if fut and not fut.done():
                        if "error" in m:
                            fut.set_exception(AcpError(json.dumps(m["error"])))
                        else:
                            fut.set_result(m.get("result", {}))
        finally:
            self._alive = False

    async def _on_incoming(self, m: dict):
        meth = m.get("method")
        params = m.get("params", {})
        if meth == "session/update":
            if self._update_cb:
                try:
                    await self._update_cb(params.get("update", {}))
                except Exception:
                    pass
        elif meth == "session/request_permission":
            if "id" in m and self._perm_cb:
                try:
                    await self._perm_cb(m["id"], params)
                except Exception:
                    await self._respond(m["id"], {"outcome": {"outcome": "cancelled"}})
            elif "id" in m:
                await self._respond(m["id"], {"outcome": {"outcome": "cancelled"}})
        elif "id" in m:
            # fs/* or terminal/* requests we didn't opt into — answer minimally
            await self._respond(m["id"], {})

    async def resolve_permission(self, req_id, option_id, allow: bool = True):
        if not self.proc:
            return
        if allow and option_id:
            out = {"outcome": {"outcome": "selected", "optionId": option_id}}
        else:
            out = {"outcome": {"outcome": "cancelled"}}
        await self._respond(req_id, out)

    async def ensure(self, model, toolsets, yolo, perm_cb):
        """(Re)start the agent if needed; resume the prior session across restarts."""
        cfg = (model or None, tuple(toolsets or []), bool(yolo))
        self._perm_cb = perm_cb
        if self.proc and self._alive and self.cfg == cfg:
            return
        async with self._starting:
            if self.proc and self._alive and self.cfg == cfg:
                return
            prior = self.session_id
            await self.stop()
            args = [HERMES, "-p", self.alias]
            if model:
                args += ["-m", model]
            if toolsets:
                args += ["-t", ",".join(toolsets)]
            if yolo:
                args += ["--yolo"]
            args += ["acp"]
            self.proc = await asyncio.create_subprocess_exec(
                *args, stdin=PIPE, stdout=PIPE, stderr=PIPE, env=_env(), cwd=HOME
            )
            self._alive = True
            self._reader = asyncio.create_task(self._read_loop())
            await asyncio.wait_for(
                await self._send(
                    "initialize",
                    {
                        "protocolVersion": 1,
                        "clientCapabilities": {
                            "fs": {"readTextFile": False, "writeTextFile": False},
                            "terminal": False,
                        },
                    },
                ),
                timeout=30,
            )
            loaded = False
            if prior:
                try:
                    await asyncio.wait_for(
                        await self._send(
                            "session/load",
                            {"sessionId": prior, "cwd": HOME, "mcpServers": []},
                        ),
                        timeout=30,
                    )
                    self.session_id = prior
                    loaded = True
                except Exception:
                    loaded = False
            if not loaded:
                r = await asyncio.wait_for(
                    await self._send("session/new", {"cwd": HOME, "mcpServers": []}),
                    timeout=30,
                )
                self.session_id = r.get("sessionId")
            self.cfg = cfg

    async def prompt(self, blocks: list, update_cb):
        if not self.session_id:
            raise AcpError("no active session")
        self._update_cb = update_cb
        try:
            return await (
                await self._send(
                    "session/prompt", {"sessionId": self.session_id, "prompt": blocks}
                )
            )
        finally:
            self._update_cb = None

    async def cancel(self):
        if self.proc and self.session_id:
            try:
                await self._send("session/cancel", {"sessionId": self.session_id}, is_req=False)
            except Exception:
                pass

    async def stop(self):
        if self._reader:
            self._reader.cancel()
            self._reader = None
        if self.proc:
            try:
                self.proc.terminate()
                await asyncio.wait_for(self.proc.wait(), 4)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None
        self._alive = False
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending = {}
