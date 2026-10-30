"""Opt-in real Codex host verification; ephemeral threads, no model turns."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import unittest

from task_management.numbering import Numbering

ROOT = Path(__file__).resolve().parents[1]


class RpcHost:
    def __init__(self,executable):
        self.process = subprocess.Popen([executable,"app-server","--stdio"],stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
        self.output, self.identity = queue.Queue(), 0
        def pump():
            for line in self.process.stdout:
                self.output.put(json.loads(line))
        def drain():
            for _ in self.process.stderr:
                pass
        self.reader = threading.Thread(target=pump,daemon=True)
        self.errors = threading.Thread(target=drain,daemon=True)
        self.reader.start();self.errors.start()

    def call(self,method,params):
        self.identity += 1
        self.process.stdin.write(json.dumps(dict(id=self.identity,method=method,params=params))+"\n")
        self.process.stdin.flush()
        while True:
            message = self.output.get(timeout=30)
            if message.get("id") == self.identity:
                if "error" in message:
                    raise RuntimeError(message["error"])
                return message["result"]

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.terminate();self.process.wait(timeout=5)
        self.reader.join(timeout=2);self.errors.join(timeout=2)
        self.process.stdout.close();self.process.stderr.close()


@unittest.skipUnless(os.environ.get("TASK_MANAGEMENT_TEST_CODEX"),"opt-in Codex CLI executable not supplied")
class CodexHostTest(unittest.TestCase):
    def test_two_real_host_sessions_discovery_and_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            data, workspace = root / "data", root / "workspace"
            workspace.mkdir()
            Numbering(data).initialize()
            host = RpcHost(os.environ["TASK_MANAGEMENT_TEST_CODEX"])
            try:
                host.call("initialize",dict(clientInfo=dict(name="task-markdown-verifier",version="0.2.0"),
                     capabilities=dict(experimentalApi=True)))
                config = {"mcp_servers.tasks":{"command":sys.executable,
                    "args":[str(ROOT / "task_management/server.py")],"env":{"TASK_MANAGEMENT_DATA":str(data)}},
                    'plugins.task-management@personal.enabled':False,"features.apps":False}
                sessions = [host.call("thread/start",dict(cwd=str(workspace),ephemeral=True,config=config))["thread"]["id"]
                            for _ in range(2)]
                self.assertNotEqual(*sessions)
                inventory = host.call("mcpServerStatus/list",dict(threadId=sessions[0],serverName="tasks"))
                self.assertEqual({t["name"] for server in inventory["data"] for t in server["tools"].values()},
                                 {"task_create","task_list","task_get","task_update"})
                def call(session,tool,**arguments):
                    result = host.call("mcpServer/tool/call",dict(threadId=session,server="tasks",tool=tool,
                        arguments={"workspace":str(workspace),"session_id":session,**arguments}))
                    self.assertFalse(result.get("isError"),str(result))
                    return result.get("structuredContent") or json.loads(result["content"][0]["text"])
                notes = "## 研究\ncache key 缺少 user ID\n## 解法\n新增 key 與回歸測試\n## 下一步\n先覆核離線模式\n"
                created = call(sessions[0],"task_create",request_id="real-request",title="Investigate",done_when="Tests pass",description=notes)
                task = created["task"]
                self.assertIn(f"{task['task_number']} Investigate — pending", created["display_summary"])
                self.assertIn("Tasks · 1 unfinished", created["display_summary"])
                self.assertEqual(call(sessions[1],"task_list")["summary"]["matching_total"],0)
                self.assertEqual(call(sessions[0],"task_get",task_id=task["task_number"])["task"]["description"],notes)
                mutation = call(sessions[0],"task_update",task_id=task["id"],expected_revision=1,actor="verification",
                                metadata={"description":notes+"## 證據\nObserved in source\n"})
                updated = mutation["task"]
                self.assertEqual(updated["revision"],2)
                self.assertIn(f"{task['task_number']} Investigate — pending", mutation["display_summary"])
                self.assertIn("pending 1", mutation["display_summary"])
                self.assertEqual(call(sessions[1],"task_list")["summary"]["matching_total"],0)
                print("REAL_CODEX_HOST_SESSION_ISOLATION_OK",flush=True)
            finally:
                host.close()
