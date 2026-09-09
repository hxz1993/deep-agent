from abc import ABC, abstractmethod
from collections import defaultdict
from random import randint
from typing import Dict, List


class AbstractServiceCache(ABC):
    @abstractmethod
    def add(self, item):
        """
        将instace增加到缓存中：对实例的add和update操作都适用
        """
        pass

    @abstractmethod
    def delete(self, item):
        """
        将instance从缓存中删除
        """
        pass

    @abstractmethod
    def punish(self, item): pass

    @abstractmethod
    def reward(self, item): pass

    @abstractmethod
    def pick_best(self): pass

    @abstractmethod
    def __len__(self) -> int: pass

    @abstractmethod
    def __iter__(self): pass


class RankedCache(AbstractServiceCache):
    def __init__(self):
        self.__rank: Dict[str, int] = {}

    def add(self, item):
        if item in self.__rank:
            return
        self.__rank[item] = 0

    def delete(self, item):
        if item not in self.__rank:
            return
        del self.__rank[item]

    def punish(self, item):
        if item not in self.__rank:
            return
        self.__rank[item] = -1

    def reward(self, item):
        if item not in self.__rank:
            return
        self.__rank[item] += 1

    def sort(self) -> List[str]:
        return [
            item[0] for item in
            sorted(self.__rank.items(), key=lambda x: -x[1])
        ]

    def pick_best(self):
        return self.sort()[0]

    def __len__(self):
        return len(self.__rank)

    def __bool__(self):
        return bool(self.__rank)

    def __iter__(self):
        return self.__rank.keys().__iter__()

    def __repr__(self):
        return f'Rank: {self.__rank}'


class RoundRobinCache(AbstractServiceCache):
    def __init__(self):
        self._active: List[str] = []
        self._dead_time: Dict[str, int] = defaultdict(int)
        self._idx = -1

    def add(self, item):
        if item not in self._active:
            self._active.append(item)
        self._dead_time.pop(item, None)

    def delete(self, item):
        if item in self._active:
            self._active.remove(item)
        self._dead_time.pop(item, None)

    def _change_idx(self):
        # len(self._active) should be larger than 0 since pick-best only be called
        # when bool(self) is true
        self._idx = (self._idx + 1) % len(self._active)

    def punish(self, item):
        self._dead_time[item] += 1

        if self._dead_time[item] >= 3:
            if item in self._active:
                self._active.remove(item)
            self._dead_time.pop(item, None)

    def reward(self, item):
        self._dead_time.pop(item, None)

    def pick_best(self):
        self._change_idx()
        return self._active[self._idx]

    def __len__(self):
        return len(self._active)

    def __bool__(self):
        return bool(self._active)

    def __iter__(self):
        return self._active.__iter__()

    def __repr__(self):
        return str(
            f'active: {self._active}, dead time: {self._dead_time}, '
            f'idx: {self._idx}'
        )


class RandomCache(RoundRobinCache):
    def _change_idx(self):
        # len(self._active) should be larger than 0 since pick-best only be called
        # when bool(self) is true
        self._idx = randint(0, len(self._active) - 1)
