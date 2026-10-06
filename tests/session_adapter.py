"""Test-only adapter: reuse legacy behavior contracts with an explicit fixed scope."""
from task_management.store import TaskStore as MarkdownStore
from task_management.numbering import Numbering
from task_management.rules import TaskError


class TaskStore(MarkdownStore):
    def create(self,*args,**kwargs):
        return super().create(*args,session_id=kwargs.pop("session_id","thread-test"),**kwargs)

    def update(self,*args,**kwargs):
        return super().update(*args,session_id=kwargs.pop("session_id","thread-test"),**kwargs)

    def list(self,*args,**kwargs):
        return super().list(*args,session_id=kwargs.pop("session_id","thread-test"),**kwargs)

    def get(self,*args,**kwargs):
        return super().get(*args,session_id=kwargs.pop("session_id","thread-test"),**kwargs)
