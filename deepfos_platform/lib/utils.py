import asyncio
import functools
import os
import sys
import time
from inspect import isfunction
from itertools import count
from typing import Union

from loguru import logger


class Inf:
    def __lt__(self, other):
        return False


class Wait:
    INF = Inf()

    def __init__(self, base: int, algo: str, maximum: int = None):
        if base < 1:
            raise ValueError("base < 1 !")
        self.base = base
        self.algo = algo
        if maximum is None:
            self.maximum = self.__class__.INF
        elif maximum < base:
            raise ValueError("maximum < base !")
        else:
            self.maximum = maximum
        self._counter = count().__next__

    def __iter__(self):
        algo = getattr(self.copy(), self.algo)
        while True:
            yield algo()

    def fixed(self):
        return self.base

    def exp_backoff(self):
        return min(self.base << self._counter(), self.maximum)

    def copy(self):
        return self.__class__(self.base, self.algo, self.maximum)


def get_ignore_case(dict_, key: str, default=None):
    if key in dict_:
        return dict_.get(key)
    lower_case_dict = {k.lower(): v for k, v in dict_.items() if isinstance(k, str)}
    return lower_case_dict.get(key.lower(), default)


def _concat_url_single(head, tail):
    slash_count = 0
    if head.endswith("/"):
        slash_count += 1
    if tail.startswith("/"):
        slash_count += 1
    if slash_count == 0:
        return head + "/" + tail
    if slash_count == 1:
        return head + tail
    return head[:-1] + "/" + tail[1:]


def concat_url(*parts):
    return functools.reduce(_concat_url_single, parts)


def auto_call(caller):
    def deco(func):
        if asyncio.iscoroutinefunction(func):
            async def wrapper(self, *args, **kwargs):
                method = getattr(self, caller)
                if asyncio.iscoroutinefunction(method):
                    await method()
                else:
                    method()
                setattr(self, func.__name__, func.__get__(self, self.__class__))
                return await func(self, *args, **kwargs)
        else:
            def wrapper(self, *args, **kwargs):
                method = getattr(self, caller)
                method()
                setattr(self, func.__name__, func.__get__(self, self.__class__))
                return func(self, *args, **kwargs)
        return functools.wraps(func)(wrapper)
    return deco


auto_setup = auto_call("setup")


def retry(
    func=None,
    retries=2,
    wait: Union[int, Wait] = 5,
    catches=(Exception,),
    fix=None,
    name=None,
    reraise=True,
    on_error=None,
):
    if func is None:
        return functools.partial(
            retry,
            retries=retries,
            wait=wait,
            catches=catches,
            fix=fix,
            name=name,
            reraise=reraise,
            on_error=on_error,
        )

    if retries < 0:
        raise ValueError("retries must be positive.")

    if isinstance(wait, int):
        wait = Wait(wait, "fixed")

    def record_retry(retried, at):
        if retried > 0 and fix is not None and callable(fix):
            fix()
        retried += 1
        logger.exception(
            f"Func: '{name or func.__qualname__}' failed. "
            f"Start {retried} times retry in {at} secs."
        )
        return retried

    if asyncio.iscoroutinefunction(func):
        async def run_func(*args, **kwargs):
            retried = 0
            waits = iter(wait)
            while True:
                try:
                    return await func(*args, **kwargs)
                except catches as e:
                    if retried >= retries:
                        if reraise:
                            raise
                        if isfunction(on_error):
                            on_error(*args, catches=e)
                        break
                    after = next(waits)
                    retried = record_retry(retried, after)
                    await asyncio.sleep(after)
    else:
        def run_func(*args, **kwargs):
            retried = 0
            waits = iter(wait)
            while True:
                try:
                    return func(*args, **kwargs)
                except catches as e:
                    if retried >= retries:
                        if reraise:
                            raise
                        if isfunction(on_error):
                            on_error(*args, catches=e)
                        break
                    after = next(waits)
                    retried = record_retry(retried, after)
                    time.sleep(after)

    return functools.wraps(func)(run_func)


def i_am(who):
    arg0 = sys.argv[0]
    name, *_ = os.path.basename(arg0).rsplit(".", maxsplit=1)
    return name == who

