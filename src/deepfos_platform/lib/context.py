import uuid

from contextvars import ContextVar
from contextlib import contextmanager


RequsetId = ContextVar('Request-Id')
TaskId = ContextVar('Task-Id')

_tid_token = None


def get_request_id():
    try:
        return RequsetId.get()
    except LookupError:
        return '-'


@contextmanager
def gen_request_id():
    token = RequsetId.set(uuid.uuid4().hex[-8:])
    try:
        yield
    finally:
        RequsetId.reset(token)


def set_task_id(task_id):
    global _tid_token
    if _tid_token is not None:
        TaskId.reset(_tid_token)

    # _tid_token = TaskId.set(current_task.request.id[-8:])
    _tid_token = TaskId.set(task_id[-8:])


def get_task_id():
    try:
        return TaskId.get()
    except LookupError:
        return '-'
