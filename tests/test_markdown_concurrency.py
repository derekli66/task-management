"""Actual process races, interrupted writers and finite lock waits."""
import multiprocessing
from pathlib import Path
import tempfile
import time
import unittest

from task_management.config import session_directory
from task_management.locking import file_lock
from task_management.numbering import Numbering
from task_management.rules import TaskError
from task_management.store import TaskStore


def compete(data, workspace, session, request, gate, results, task=None):
    gate.wait(5)
    try:
        store = TaskStore(data)
        result = store.update(workspace,task,1,session_id=session,actor="worker",action="start") if task else \
            store.create(workspace,request,"Task","Done",session_id=session)
        results.put(("ok",result["task"]))
    except TaskError as exc:
        results.put((exc.code,None))


def held_writer(data, workspace, task, signal, after_commit):
    store = TaskStore(data)
    def fault(stage):
        if stage == ("committed" if after_commit else "before_replace"):
            signal.send("ready")
            time.sleep(30)
    store.fault = fault
    store.update(workspace,task,1,session_id="same",actor="worker",action="start")


class ProcessTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.workspace = str(self.root / "workspace")
        Path(self.workspace).mkdir()
        self.data = self.root / "data"
        Numbering(self.data).initialize()
        self.context = multiprocessing.get_context("spawn")

    def race(self, inputs, task=None):
        gate, results = self.context.Event(), self.context.Queue()
        processes = [self.context.Process(target=compete,args=(self.data,self.workspace,*values,gate,results,task))
                     for values in inputs]
        try:
            for process in processes:
                process.start()
            gate.set()
            output = [results.get(timeout=10) for _ in processes]
            for process in processes:
                process.join(10)
                self.assertEqual(process.exitcode,0)
            return output
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate();process.join()
            results.close()

    def test_duplicate_request_and_global_number_races(self):
        result = self.race([("same","key")]*4)
        self.assertEqual({s for s,t in result},{"ok"})
        self.assertEqual(len({t["id"] for s,t in result}),1)
        result = self.race([(f"session-{n}","key") for n in range(4)])
        self.assertEqual({t["task_number"] for s,t in result},{"#2","#3","#4","#5"})

    def test_only_one_writer_can_start_the_same_revision(self):
        task = TaskStore(self.data).create(self.workspace,"key","Task","Done",session_id="same")["task"]
        result = self.race([("same","unused")]*2,task=task["id"])
        self.assertEqual(sorted(s for s,t in result),["ok","revision_conflict"])

    def test_revision_race_kill_before_and_after_commit(self):
        for committed in (False,True):
            store = TaskStore(self.data)
            task = store.create(self.workspace,str(committed),"Task","Done",session_id="same")["task"]
            reader, writer = self.context.Pipe(False)
            process = self.context.Process(target=held_writer,args=(self.data,self.workspace,task["id"],writer,committed))
            process.start()
            try:
                self.assertTrue(reader.poll(10))
                reader.recv()
                process.terminate();process.join(5)
                result = store.get(self.workspace,task["id"],session_id="same")
                self.assertEqual(result["task"]["revision"],2 if committed else 1)
                self.assertEqual(len(result["events"]),2 if committed else 1)
                if not committed:
                    store.update(self.workspace,task["id"],1,session_id="same",actor="recovery",action="start")
            finally:
                if process.is_alive():
                    process.terminate();process.join()
                reader.close();writer.close()

    def test_lock_timeout_does_not_modify_task(self):
        store = TaskStore(self.data,timeout=0.02)
        task = store.create(self.workspace,"key","Task","Done",session_id="same")["task"]
        with file_lock(session_directory(self.workspace,"same") / ".lock"):
            with self.assertRaises(TaskError) as caught:
                store.update(self.workspace,task["id"],1,session_id="same",actor="test",action="start")
        self.assertEqual(caught.exception.code,"database_busy")
        self.assertEqual(store.get(self.workspace,task["id"],session_id="same")["task"]["revision"],1)
