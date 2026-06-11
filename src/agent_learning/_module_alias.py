from __future__ import annotations

import importlib
from importlib.machinery import ModuleSpec
import sys
import types
from typing import Mapping


class LazyModuleAlias(types.ModuleType):
    """Lazy public module alias for vendored engine submodules."""

    def __init__(self, public_name: str, target_name: str, *, package: bool = False):
        super().__init__(public_name)
        self.__dict__["_target_name"] = target_name
        self.__dict__["__package__"] = public_name if package else public_name.rpartition(".")[0]
        self.__dict__["__spec__"] = ModuleSpec(public_name, loader=None, is_package=package)
        if package:
            self.__dict__["__path__"] = []

    def _target(self) -> types.ModuleType:
        return importlib.import_module(self.__dict__["_target_name"])

    def __getattr__(self, name: str) -> object:
        return getattr(self._target(), name)

    def __dir__(self) -> list[str]:
        return sorted(set(super().__dir__()) | set(dir(self._target())))


def install_lazy_module_aliases(
    root_name: str,
    aliases: Mapping[str, str],
    *,
    package_aliases: set[str] | None = None,
) -> None:
    """Register ``root_name.<alias>`` modules that lazily import target modules."""

    packages = package_aliases or set()
    root = sys.modules.get(root_name)
    if root is not None and not hasattr(root, "__path__"):
        root.__path__ = []  # type: ignore[attr-defined]

    for alias, target in aliases.items():
        public_name = f"{root_name}.{alias}"
        module = sys.modules.get(public_name)
        if not isinstance(module, LazyModuleAlias):
            module = LazyModuleAlias(public_name, target, package=alias in packages)
            sys.modules[public_name] = module
        parent_name, _, child_name = public_name.rpartition(".")
        parent = sys.modules.get(parent_name)
        if parent is not None:
            setattr(parent, child_name, module)
