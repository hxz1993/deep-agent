import inspect
import os
import re
import warnings
from enum import Enum, auto
from functools import cached_property
from typing import (
    Iterable, Any, Tuple, Union,
    List, Callable, Dict, Optional,
    NamedTuple, Coroutine, TypeVar, Generic, Type
)

from loguru import logger

from ..lib.constants import BOOLEAN_STATES, ENV_TEST_MODE
from .reloader import OnChangeT, EnvironSource, EnvironMonitor, EnvFile, EnvContainer
from .spring import SpringConfig
from .nacos import NacosConfig
from .apollo import Apollo

__all__ = [
    'Option', 'Category', 'GlobalOptions',
    'Action', 'SKIP',
    'EnvResult', 'EnvGetterT', 'OptReloaderT', 'ReloadOptT',
]

PROTECTED_VALUE = 6 * '*'
_GLOBAL_OPTION: 'GlobalOptions' = None  # noqa


class EnvResult(NamedTuple):
    found: bool
    value: str = ""
    source: Optional[str] = None


class Action(Enum):
    PASS = auto()
    WARN = auto()
    RAISE = auto()


SKIP = object()
ValTypeT = TypeVar('ValTypeT')
CASTABLE = [int, float]
EnvGetterT = Callable[[str], EnvResult]
OptReloaderT = Callable[[Any], Union[Any, Coroutine[Any, Any, Any]]]
ReloadOptT = Union[bool, OptReloaderT, None]


# -----------------------------------------------------------------------------
# Exceptions
class BaseOptionError(Exception):
    pass


class OptionNotSetError(BaseOptionError):
    pass


class OptionTypeError(BaseOptionError):
    pass


class OptionValueError(BaseOptionError):
    pass


class OptionInitError(BaseOptionError):
    pass


# -----------------------------------------------------------------------------
# Option class
class Option(Generic[ValTypeT]):
    """
    配置项基类

    Args:
        default: 默认值
        val_type: 允许的类型, 多类型时传入tuple of type
        val_choices: 允许的取值
        extra_check: 其他的校验，可传入callerable，设定值将作为参数传入
        if_unset: 当配置项未设置就被使用时，对应的行为。

            - Action.PASS: 忽略
            - Action.WARN: 抛出warning
            - Action.RAISE: 报错

    Example:
        .. code-block:: python

            class _CubeOption(_Category):
                key = _Option(False, val_type=bool,
                    write_warning=[(True: "warn！！！")])

        `key` 默认值为 `False`, 只能设置为 `bool` 类型。
        并且在设置为 `True` 时会提示warning信息。

    """
    def __init__(
        self,
        default: Any,
        val_type: Type[ValTypeT] = None,
        val_choices: Tuple[ValTypeT, ...] = (),
        extra_check: Union[Callable[[ValTypeT], Any], Iterable[Callable[[ValTypeT], Any]]] = (),
        if_unset: Action = Action.PASS,
        convertor: Callable[[Any], ValTypeT] = None,
        protected: bool = False,
        bind_env: Union[str, List[str]] = None,
        reload_opt: ReloadOptT = None,
        respect_env_prefix: bool = True
    ):
        if isinstance(extra_check, Iterable):
            self.extra_check = tuple(extra_check)
        else:
            self.extra_check = (extra_check, )

        self.val_type = val_type
        self.val_choices = val_choices
        self.default = default
        self.unset_action = if_unset
        self.protected = protected
        if isinstance(bind_env, str):
            self.bind_env = [bind_env]
        else:
            self.bind_env = bind_env
        self.convertor = convertor
        self.set_by = None
        self.reload_enabled = bool(reload_opt)
        self.reload = reload_opt if callable(reload_opt) else None
        self.respect_env_prefix = respect_env_prefix

    def __set_name__(self, owner, name):
        self._var_name = '__' + name
        self._display_name = name

    def __get__(self, instance, owner) -> ValTypeT:
        if instance is None:
            return self

        if self.unset_action is Action.RAISE:
            raise OptionNotSetError(f"Option '{instance}.{self}' has not yet been set.")
        elif self.unset_action is Action.WARN:
            warnings.warn(f"Option '{instance}.{self}' has not yet been set.")

        try:
            return getattr(instance, self._var_name)
        except AttributeError:
            if isinstance(instance, Category):
                instance._resolve_option_from_env(self)

            try:
                return getattr(instance, self._var_name)
            except AttributeError:
                pass

            rslt = self.default
            if isinstance(rslt, Option):
                return rslt.__get__(instance, owner)

            if rslt is ...:
                if isinstance(instance, Category) and self.bind_env:
                    bind_env = list(instance._finalize_bind_env(self))
                    raise OptionNotSetError(
                        f"Option '{instance._name}.{self}' has not been set. "
                        f"None of environment variable: {bind_env} is set."
                    )
                raise OptionNotSetError(f"Option '{instance}.{self}' has not yet been set.")

            setattr(instance, self._var_name, rslt)
            return rslt

    def __set__(self, instance, value):
        if self.convertor is not None:
            ori_val = value
            value = self.convertor(value)
            if value is SKIP:
                logger.opt(depth=1).warning(f"Skip setting {instance}.{self} as {ori_val}")
                return

        if self.val_type is not None:
            if not isinstance(value, self.val_type):
                raise OptionTypeError(
                    f"'{instance}.{self}' can only be set as type "
                    f"'{self.val_type}', not '{type(value)}'.")

        if self.val_choices:
            if value not in self.val_choices:
                raise OptionValueError(
                    f"'{instance}.{self}' can only be "
                    f"chosen from {self.val_choices!r}.")

        self.validate(value)

        setattr(instance, self._var_name, value)
        if self.protected:
            logger.opt(depth=1).info(f"Set option: {instance}.{self}={PROTECTED_VALUE}")
        else:
            logger.opt(depth=1).info(f"Set option: {instance}.{self}={value}")

        if self.unset_action is not Action.PASS:
            self.unset_action = Action.PASS

    def coerce(self, value: str) -> Union[ValTypeT, str]:
        if self.val_type is bool:
            return BOOLEAN_STATES[value.lower()]
        elif self.val_type in CASTABLE:
            return self.val_type(value)
        return value

    def __str__(self):
        return self._display_name

    def reset(self, instance):
        if self.default is ...:
            raise OptionValueError(
                f"Cannot reset option: {instance}.{self} "
                f"because it does not have a default value.")
        try:
            delattr(instance, self._var_name)
        except AttributeError:
            pass

    def validate(self, value):
        try:
            for do_check in self.extra_check:
                do_check(value)
        except Exception as e:
            raise OptionValueError(
                f"Cannot set {self._display_name}={value}. "
                f"Cause: {e!r}")


# -----------------------------------------------------------------------------
# Nested Option
class Category:
    _ENV_PREFIX = ''
    RE_ENV = re.compile(r"\${(.+?)}")

    def set_env_prefix(self, prefix: str):
        self.__class__._ENV_PREFIX = prefix

    def __get__(self, instance, owner):
        if instance is None:
            return self
        try:
            inst = getattr(instance, self._var_name)
        except AttributeError:
            inst = self.__class__()
            inst._name = self._name
            inst._root = instance
            setattr(instance, self._var_name, inst)
        return inst

    def __set__(self, instance, value):
        raise TypeError("Category instance cannot be set.")

    def __set_name__(self, owner, name):
        self._name = name
        self._var_name = '__' + name

    def __str__(self):
        return self._name

    def _finalize_bind_env(self, option: Option) -> Iterable[str]:
        if self._ENV_PREFIX and option.respect_env_prefix:
            return [self._ENV_PREFIX + env for env in option.bind_env]
        else:
            return option.bind_env

    def _env_binding_enabled(self) -> bool:
        return not BOOLEAN_STATES.get(os.getenv(ENV_TEST_MODE, "False"))

    def _env_getter(self) -> Optional[EnvGetterT]:
        root = getattr(self, '_root', None) or _GLOBAL_OPTION
        if root is None:
            return None
        return root._env_getter

    def _apply_env_to_option(self, option: Option, env_getter: EnvGetterT) -> bool:
        if not option.bind_env:
            return False

        for env in self._finalize_bind_env(option):
            configured, v, source = env_getter(env)
            option.set_by = source
            if configured:
                option.__set__(self, option.coerce(v))
                return True
        return False

    def _resolve_option_from_env(self, option: Option) -> bool:
        if not self._env_binding_enabled():
            return False
        if not option.bind_env:
            return False
        env_getter = self._env_getter()
        if env_getter is None:
            return False
        return self._apply_env_to_option(option, env_getter)

    def set_default(self, env_getter: EnvGetterT):
        for name, option in self._options.items():
            if not option.bind_env:
                continue

            if not self._apply_env_to_option(option, env_getter):
                bind_env = list(self._finalize_bind_env(option))
                if self._get_option(name) is ...:
                    raise KeyError(f"None of environment variable: {bind_env} is set.")

    @property
    def _options(self) -> Dict[str, Option]:
        return {
            k: v for k, v in self.__class__.__dict__.items()
            if isinstance(v, Option)
        }

    def to_dict(self):
        return {
            k: self._get_option(k) for
            k in self._options
        }

    def show_options(self, option=None):
        name = self._name
        options = self._options

        if option is None:
            print(f"{name}\n{len(name)*'-'}")
            for opt in options:
                print(f"{opt}: {self._get_option(opt)}")
            print()
        else:
            if option not in options:
                raise KeyError(f"No such option: '{name}.{option}'")
            print(f"{name}.{option}: {self._get_option(option)}")

    def _get_option(self, option: str):
        try:
            return getattr(self, option)
        except BaseOptionError:
            return getattr(self.__class__, option).default

    def copy_from(self, other):
        if type(other) is not self.__class__:
            raise TypeError(
                f"Cannot make copy from instance of {type(other)}, "
                f"expect {self.__class__}.")

        if not hasattr(other, '_name'):
            raise ValueError(
                "Could not make a copy because target category is not "
                "properly initialized. Category is supposed to be a class variable.")

        setattr(self, '_name', getattr(other, '_name'))
        for attr, option in self._options.items():
            try:
                option.__set__(self, getattr(other, attr))
            except BaseOptionError:
                continue

    def reset(self, option: str = None):
        options = self._options

        if option is None:
            for opt in options.values():
                opt.reset(self)

        else:
            opt = options.get(option)
            if opt is not None:
                opt.reset(self)

    def collect_callbacks(self) -> Dict[str, OnChangeT]:
        def create_on_change(opt: Option) -> OnChangeT:
            if (reload := opt.reload) is None:
                async def on_change(value: str):
                    value = opt.coerce(value)
                    opt.__set__(self, value)

            elif inspect.iscoroutinefunction(reload):
                async def on_change(value: str):
                    value = opt.coerce(value)
                    opt.__set__(self, value)
                    await reload(value)
            else:
                async def on_change(value: str):
                    value = opt.coerce(value)
                    opt.__set__(self, value)
                    reload(value)

            return on_change

        callbacks: Dict[str, OnChangeT] = {}

        for option in self._options.values():
            if option.bind_env is None or not option.reload_enabled:
                continue
            cb = create_on_change(option)
            callbacks.update(dict.fromkeys(self._finalize_bind_env(option), cb))

        return callbacks

    def validate(self):
        for name, opt in self._options.items():
            opt.validate(self._get_option(name))


# -----------------------------------------------------------------------------
# Options
class GlobalOptions:
    def __init__(self):
        self._apollo = Apollo()
        self._spring = SpringConfig()
        self._nacos = NacosConfig()

    @property
    def _categories(self) -> Dict[str, Category]:
        return {
            k: getattr(self, k) for k, v in self.__class__.__dict__.items()
            if isinstance(v, Category)
        }

    def show_options(self, category=None):
        categories = self._categories

        if category is None:
            for ctgy in categories.values():
                ctgy.show_options()
        else:
            if category not in categories:
                raise KeyError(f"No such category: '{category}'.")
            categories[category].show_options()

    def __copy__(self):
        cp = self.__class__()
        for attr, category in self._categories.items():
            getattr(cp, attr).copy_from(category)
        return cp

    def validate(self):
        for ctgy in self._categories.values():
            ctgy.validate()

    def collect_callbacks(self) -> Dict[str, OnChangeT]:
        callbacks = {}
        for cat in self._categories.values():
            callbacks.update(cat.collect_callbacks())
        return callbacks

    async def start_monitor(self):
        if not (sources := self.env_sources):
            return
        if not (callbacks := self.collect_callbacks()):
            return

        await EnvironMonitor(sources, callbacks).start()

    @cached_property
    def _env_getter(self) -> EnvGetterT:
        return self.create_env_getter()

    def create_env_getter(self) -> EnvGetterT:
        def chain_getter(getter_a: EnvGetterT, getter_b: EnvGetterT) -> EnvGetterT:
            def chained(env):
                res = getter_a(env)
                if not res.found:
                    return getter_b(env)
                return res
            return chained

        def create_flat_getter(container: EnvContainer) -> EnvGetterT:
            def getter(env: str):
                if env in container:
                    return EnvResult(True, container.get(env), env)
                return EnvResult(False)
            return getter

        if self._apollo.enabled:
            return chain_getter(create_flat_getter(self._apollo), create_flat_getter(os.environ))
        if self._spring.enabled:
            return chain_getter(create_flat_getter(self._spring), create_flat_getter(os.environ))
        if self._nacos.enabled:
            return chain_getter(create_flat_getter(self._nacos), create_flat_getter(os.environ))
        return create_flat_getter(os.environ)

    def load_env(self):
        for attr in self._categories.values():
            attr.set_default(self._env_getter)

    @property
    def env_sources(self) -> List[EnvironSource]:
        return []
