from reprlib import Repr
from ..options import OPTION


def enrich_repr(repr_obj: Repr, factor: int) -> Repr:
    repr_obj.maxlevel *= factor
    repr_obj.maxtuple *= factor
    repr_obj.maxlist *= factor
    repr_obj.maxarray *= factor
    repr_obj.maxdict *= factor * 5
    repr_obj.maxset *= factor
    repr_obj.maxfrozenset *= factor
    repr_obj.maxdeque *= factor
    repr_obj.maxstring *= factor
    repr_obj.maxlong *= factor
    repr_obj.maxother *= factor
    return repr_obj


aRepr = Repr()

if (level := OPTION.log.content) == "COMPLETE":
    repr = repr
elif level == "CONCISE":
    repr = aRepr.repr
else:
    repr = enrich_repr(aRepr, 2).repr
