"""Lossless readable notes, scoped identities and integrity contracts."""
import copy
import unittest
import uuid

from task_management.markdown import decode_document, encode_document, seal_task
from task_management.rules import TaskError


def document(notes=""):
    task = dict(id="11111111-1111-4111-8111-111111111111", task_number="#1",
        request_id="one", initial_payload="{}", title="中文\nTitle", description=notes,
        done_when="驗證通過", priority="normal", status="pending", owner=None,
        block_reason=None, completion_evidence=None, revision=1,
        created_at="20261007_000000", updated_at="20261007_000000", dependencies=[])
    seal_task(task)
    event = dict(seq=1, task_id=task["id"], revision=1, action="create", actor="test",
                 workspace="/fixture/workspace",
                 reason=None, evidence=None, before=None, after={"id":task["id"]},
                 created_at=task["created_at"])
    return dict(metadata=dict(format_version=1, workspace="/fixture/workspace", session_id="thread-a",
        numbering_namespace="22222222-2222-4222-8222-222222222222",
        document_id="33333333-3333-4333-8333-333333333333", generation=1),
        tasks=[task], events=[event])


class MarkdownFormatTest(unittest.TestCase):
    def test_roundtrip_arbitrary_markdown_and_sparse_history(self):
        for notes in ("", "中文\n", "#### 解法\n\n```python\nprint('hi')\n```\n",
                "```\n## #7\n<details>\n```\n\n### 工作筆記\n", "\r\n多行\r\n\x00"):
            with self.subTest(notes=notes):
                original = document(notes)
                text = encode_document(original)
                self.assertIn(notes, text)
                self.assertEqual(decode_document(text), original)

    def test_external_note_edit_and_unknown_version_are_not_reset(self):
        text = encode_document(document("Investigated root cause"))
        with self.assertRaises(TaskError) as error:
            decode_document(text.replace("Investigated root cause", "Changed the root cause"))
        self.assertEqual(error.exception.code, "storage_error")
        data = document()
        data["metadata"]["format_version"] = 99
        with self.assertRaises(TaskError) as error:
            decode_document(encode_document(data))
        self.assertEqual(error.exception.code, "unsupported_schema")

    def test_derived_readiness_is_not_an_integrity_field(self):
        original = document("研究與步驟")
        task = copy.deepcopy(original["tasks"][0])
        task.update(ready=True, unfinished_dependencies=[], workspace="/other", session_id="thread-a")
        seal_task(task)
        self.assertEqual(task["integrity_digest"], original["tasks"][0]["integrity_digest"])

    def test_long_dependency_chain_is_not_limited_by_python_recursion(self):
        data = document()
        previous = data["tasks"][0]["id"]
        for number in range(2,1050):
            task = copy.deepcopy(data["tasks"][0])
            task.update(id=str(uuid.uuid4()),task_number=f"#{number}",request_id=str(number),dependencies=[previous])
            seal_task(task)
            event = copy.deepcopy(data["events"][0])
            event.update(seq=number,task_id=task["id"],after={"id":task["id"]})
            data["tasks"].append(task);data["events"].append(event)
            previous = task["id"]
        self.assertEqual(decode_document(encode_document(data)),data)
