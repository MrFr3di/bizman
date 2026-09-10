"""Compatibility shim for canonical bizman.collector.events."""

from importlib import import_module as _import_module

from bizman.collector.events import *  # noqa: F401,F403

_canonical = _import_module("bizman.collector.events")


def __getattr__(name: str):
    return getattr(_canonical, name)


def __dir__():
    return sorted(set(globals()) | set(dir(_canonical)))


__all__ = getattr(
    _canonical,
    "__all__",
    tuple(name for name in dir(_canonical) if not name.startswith("_")),
)
