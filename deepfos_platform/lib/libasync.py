import asyncio
import concurrent.futures
import functools
import threading
import warnings
import weakref

from typing import Awaitable, Callable, Coroutine, Generic, Optional, TypeVar, Any, TYPE_CHECKING

from loguru import logger
from starlette.concurrency import run_in_threadpool


RT = TypeVar('RT')

_bridge_pool = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix='async-bridge'
)


def run_background(
    coro: Coroutine[Any, Any, Any],
    *,
    name: str = 'platform-bg',
) -> threading.Thread:
    """Run a long-lived coroutine in a daemon thread (e.g. master heartbeat)."""

    def _target():
        try:
            asyncio.run(coro)
        except Exception:
            logger.exception(f"Background task [{name}] failed")

    thread = threading.Thread(target=_target, daemon=True, name=name)
    thread.start()
    return thread


def run_sync(coro: Coroutine[Any, Any, RT]) -> RT:
    """Block until coro completes. Uses asyncio.run when no loop is running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    return _bridge_pool.submit(asyncio.run, coro).result()


class EventLoop:
    """Deprecated: use run_sync / run_background. Removed in 0.3.0."""

    def run(self, coro):
        warnings.warn(
            'evloop.run is deprecated; use deepfos_platform.lib.libasync.run_sync',
            DeprecationWarning,
            stacklevel=2,
        )
        return run_sync(coro)

    def apply(self, coro):
        warnings.warn(
            'evloop.apply is deprecated; use deepfos_platform.lib.libasync.run_background',
            DeprecationWarning,
            stacklevel=2,
        )
        return run_background(coro, name='evloop-shim')

    def post_fork(self):
        warnings.warn(
            'evloop.post_fork is deprecated; use deepfos_platform.runtime.reset_worker_state',
            DeprecationWarning,
            stacklevel=2,
        )
        from ..runtime.worker_state import reset_worker_state

        reset_worker_state()


evloop = EventLoop()


class Node:
    __slots__ = ('next', 'value', '_prev', '__weakref__')

    def __init__(self, value=None):
        self.value = value
        self.next: Optional[Node] = None
        self._prev = None

    @property
    def prev(self):
        if not self._prev:
            return self._prev
        return self._prev()

    @prev.setter
    def prev(self, value):
        if value is None:
            self._prev = None
        else:
            self._prev = weakref.ref(value)

    def __bool__(self):
        return True

    def __repr__(self):
        return repr(self.value)


class LinkedList:
    def __init__(self):
        self.tail = self.root = Node()

    def __len__(self):
        return sum(1 for _ in self)

    def append(self, node: Node):
        self.tail.next = node
        node.prev = self.tail
        self.tail = node

    def wrap_append(self, value):
        node = Node(value)
        self.append(node)
        return node

    def remove(self, node: Node):
        _prev = node.prev
        _next = node.next

        _prev.next = _next

        if _next is None:
            self.tail = _prev
        else:
            _next.prev = _prev

    def __repr__(self):
        return "[{}]".format(', '.join(repr(item) for item in self))

    def __iter__(self):
        node = self.root
        while node := node.next:
            yield node.value


class AsyncTimer(threading.Thread):
    def __init__(self, log=None):
        super().__init__()
        self._loop = asyncio.get_event_loop()
        self._tasks = LinkedList()
        self._logger = log or logger

    def run(self) -> None:
        self._loop.run_forever()
        self._logger.debug("Loop stopped.")

    def call_soon(self, coro):
        r = asyncio.run_coroutine_threadsafe(coro, self._loop)
        tasks = self._tasks
        if not r.done():
            node = tasks.wrap_append(r)
            r.add_done_callback(lambda _: tasks.remove(node))
        return r

    def join(self, timeout=None):
        self._logger.debug(f"Waiting {len(self._tasks)} tasks to finish...")
        for t in self._tasks:
            try:
                if not t.done():
                    t.result(timeout)
            except Exception:  # noqa
                logger.exception(f"Waiting task: {t} failed")
        self._logger.debug("All tasks finished. Stopping event loop...")
        self._loop.call_soon_threadsafe(self._loop.stop)
        return super().join(timeout)

    def call_later(self, sec, coro):
        async def wrapper():
            await asyncio.sleep(sec)
            return await coro
        return self.call_soon(wrapper())


def periodic_task(
    func=None,
    interval: float = 5.0,
    loop_when: Callable = (lambda: True),
    stop_on_error: bool = False,
):
    if func is None:
        return functools.partial(
            periodic_task,
            interval=interval, loop_when=loop_when,
            stop_on_error=stop_on_error
        )

    is_coro = asyncio.iscoroutinefunction(func)

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        async def task_body():
            while loop_when():
                try:
                    if is_coro:
                        await func(*args, **kwargs)
                    else:
                        await run_in_threadpool(func, *args, **kwargs)
                except Exception as e:
                    if stop_on_error:
                        raise e from None
                    else:
                        logger.exception('调用定时任务失败')

                await asyncio.sleep(interval)

        asyncio.create_task(task_body())

    return wrapper


class async_property(Generic[RT]):
    def __init__(self, func: Callable[[Any], Awaitable[RT]]):
        self.func = func
        self.lock = threading.RLock()

    def __set_name__(self, owner: Any, name: str):
        self._name = name

    def __get__(self, instance, owner) -> Awaitable[RT]:
        if instance is None:
            raise RuntimeError('async_property should be called from instance.')

        with self.lock:
            if self._name not in instance.__dict__:
                instance.__dict__[self._name] = asyncio.ensure_future(self.func(instance))

        return instance.__dict__[self._name]

    if TYPE_CHECKING:
        def __await__(self) -> Awaitable[RT]: ...
