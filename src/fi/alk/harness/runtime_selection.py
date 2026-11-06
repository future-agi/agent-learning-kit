from __future__ import annotations

import errno
import re
import os
import stat
from contextvars import ContextVar
from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from .contract import Runtime
from .source_folder import contains_or_is_above, is_within


COMPOSE_SERVICE_OUTSIDE_FOLDER = "runtime_compose_service_outside_subfolder"


class RuntimeSelectionError(ValueError):
    pass


@dataclass(frozen=True)
class NativeSelection:
    workdir: Path
    command: tuple[str, ...]


@dataclass(frozen=True)
class ComposeSelection:
    compose_file: Path
    control_service: str
    services: dict[str, Any]
    document: dict[str, Any]
    source_files: tuple[Path, ...]


# Every file a Compose selection reads, includes too.
_COMPOSE_BYTES = 2 * 1024 * 1024


def select_runtime(
    root: Path,
    runtime: Runtime | dict[str, Any],
    *,
    public_environment: dict[str, str] | None = None,
    validate_environment_dependencies: bool = True,
    compose_bytes: int = _COMPOSE_BYTES,
) -> NativeSelection | ComposeSelection:
    # One budget for the whole selection: a budget per call let many services each stay under it.
    token = _SELECTION_BUDGET.set([_STRUCTURE_NODES])
    try:
        return _select_runtime(
            root,
            runtime,
            public_environment=public_environment,
            validate_environment_dependencies=validate_environment_dependencies,
            compose_bytes=compose_bytes,
        )
    finally:
        _SELECTION_BUDGET.reset(token)


def _select_runtime(
    root: Path,
    runtime: Runtime | dict[str, Any],
    *,
    public_environment: dict[str, str] | None,
    validate_environment_dependencies: bool,
    compose_bytes: int,
) -> NativeSelection | ComposeSelection:
    raw = runtime.model_dump() if isinstance(runtime, Runtime) else runtime
    interface = raw.get("interface") or {}
    runtime = Runtime.model_validate(
        {key: value for key, value in raw.items() if key != "interface"}
    )
    root = root.resolve()
    workdir = (root / (runtime.workdir or ".")).resolve()
    if not workdir.is_relative_to(root) or not workdir.is_dir():
        raise RuntimeSelectionError("runtime_workdir_invalid")
    if runtime.execution == "native":
        return NativeSelection(workdir, tuple(runtime.command))
    if runtime.compose_file:
        compose = (root / runtime.compose_file).resolve()
        if not compose.is_relative_to(root) or not compose.is_file():
            raise RuntimeSelectionError("runtime_compose_file_invalid")
    else:
        # A Compose file at the repository root belongs to the whole repository, not to the
        # agent folder, so automatic lookup reads the folder alone.
        lookup = root / runtime.source_subfolder if runtime.source_subfolder else root
        matches = [
            lookup / name
            for name in (
                "compose.yaml",
                "compose.yml",
                "docker-compose.yaml",
                "docker-compose.yml",
            )
            if (lookup / name).is_file()
        ]
        if len(matches) > 1:
            raise RuntimeSelectionError(
                "compose_ambiguous: " + ", ".join(p.name for p in matches)
            )
        if not matches:
            return NativeSelection(workdir, tuple(runtime.command))
        compose = matches[0]
    compose = compose.resolve()
    if not compose.is_relative_to(root):
        raise RuntimeSelectionError("runtime_compose_file_invalid")
    if runtime.compose_overrides:
        from .compose_merge import merge_compose

        try:
            body = merge_compose(
                root,
                [compose, *(root / name for name in runtime.compose_overrides)],
                public_environment=public_environment,
            )
        except ValueError as exc:
            raise RuntimeSelectionError(str(exc)) from None
        source_files = (compose, *((root / name).resolve() for name in runtime.compose_overrides))
        service_bases = {name: compose.parent for name in body["services"]}
    else:
        body, service_bases, source_files = _load_compose(
            root, compose, public_environment or {}, compose_bytes
        )
    if not isinstance(body, dict) or not isinstance(body.get("services"), dict):
        raise RuntimeSelectionError("compose_invalid: services must be an object")
    services = {
        name: dict(service)
        for name, service in body["services"].items()
        if isinstance(service, dict)
    }
    if len(services) != len(body["services"]):
        raise RuntimeSelectionError("compose_invalid: each service must be an object")
    control = runtime.service
    if control or (
        runtime.command
        and interface
        and interface.get("kind") == "http"
        and interface.get("port")
    ):
        control, services = _runtime_compose_services(
            compose.parent,
            services,
            str(workdir.relative_to(compose.parent))
            if workdir.is_relative_to(compose.parent)
            else str(workdir),
            int(interface.get("port") or 0),
            control,
            public_environment or {},
            validate_environment_dependencies,
            service_bases,
            additional_services=runtime.additional_services,
        )
    for name in runtime.compose_image_services:
        if name not in services:
            raise RuntimeSelectionError(f"compose_image_service_unselected: {name}")
        services[name].pop("build", None)
    if runtime.source_subfolder:
        # Every service the stack runs, not only the control one: a helper that builds a sibling
        # ships the sibling's files, and its keys go unasked.
        folder = (root / runtime.source_subfolder).resolve()
        # Services that share a build (a YAML alias costs nothing to repeat) are checked once.
        checked: dict[tuple[Path, str, str], bool] = {}
        asked = set(filter(None, (public_environment or {}).get("COMPOSE_PROFILES", "").split(",")))
        for name, service in services.items():
            # With no control service the whole file runs, but `docker compose up` starts a
            # service behind a profile only when COMPOSE_PROFILES asks for one of its profiles.
            profiles = service.get("profiles")
            if (
                not control
                and profiles
                and "*" not in asked
                and not (isinstance(profiles, list) and asked & {str(value) for value in profiles})
            ):
                continue
            if _builds_outside_folder(
                service,
                service_bases.get(name, compose.parent),
                folder,
                public_environment or {},
                checked,
            ):
                raise RuntimeSelectionError(f"{COMPOSE_SERVICE_OUTSIDE_FOLDER}: {name}")
    services = {
        name: {
            key: _interpolate_structure(value, public_environment or {})
            if key in {"build", "image", "ports", "depends_on", "deploy", "volumes"}
            and not (runtime.compose_overrides and key == "volumes")
            else value
            for key, value in service.items()
        }
        for name, service in services.items()
    }
    for name in runtime.compose_image_services:
        image = services[name].get("image")
        if (
            not isinstance(image, str)
            or not image.strip()
            or "$" in image
            or any(char.isspace() for char in image)
        ):
            raise RuntimeSelectionError(f"compose_image_reference_invalid: {name}")
    for name, service in services.items():
        if service_bases[name] != compose.parent:
            _rebase_service(
                service,
                service_bases[name],
                compose.parent,
                root,
                public_environment or {},
            )
    return ComposeSelection(compose, control, services, body, source_files)


def _builds_outside_folder(
    service: Any,
    base: Path,
    folder: Path,
    public_environment: dict[str, str],
    checked: dict[tuple[Path, str, str], bool] | None = None,
) -> bool:
    """True when the service builds a folder that is neither the agent's nor above it."""
    if not isinstance(service, dict) or not service.get("build"):
        return False
    build = _interpolate_structure(service["build"], public_environment)
    context = str(build.get("context", ".") if isinstance(build, dict) else build)
    if "://" in context:
        return False
    dockerfile = build.get("dockerfile") if isinstance(build, dict) else None
    key = (base, context, str(dockerfile or ""))
    if checked is not None and key in checked:
        return checked[key]
    built = _resolved(base, context)
    places = [built]
    if dockerfile:
        # A root context with a sibling's Dockerfile still builds the sibling.
        places.append(_resolved(built, str(dockerfile)).parent)
    outside = any(not contains_or_is_above(place, folder) for place in places)
    if checked is not None:
        checked[key] = outside
    return outside


def _resolved(base: Path, value: str) -> Path:
    """``base / value`` resolved, each lookup on disk charged to the selection's budget.

    A path past the system's limit can't be opened anyway, so it's refused.
    """
    if len(value) > _LONGEST_PATH:
        raise RuntimeSelectionError("compose_source_path_invalid")
    return _charged_resolve(base / value)


def _charged_resolve(path: Path) -> Path:
    """``path.resolve()``, each lookup on disk charged to the selection's budget.

    Resolving looks up each part on disk, and follows a link to every part of its target, fresh
    on each call; a link can make a short path written in the Compose file cost hundreds of
    lookups. So this resolves as ``os.path.realpath`` does, and charges each lookup as it makes
    it. Each lookup walks the path up to its part, so a deep part costs more: about 2 us at 100
    parts and 5 us at 400 on 3.12.3. Linux allows 2,000.
    """
    budget = _SELECTION_BUDGET.get()

    def charge(looked_up: str) -> None:
        if budget is None:
            return
        budget[0] -= 1 + looked_up.count("/") // 256
        if budget[0] < 0:
            raise RuntimeSelectionError("compose_structural_interpolation_limit")

    resolved, _ = _join_resolved("", os.fspath(path), {}, charge)
    resolved = os.path.abspath(resolved)
    # As Path.resolve does: realpath leaves a link loop in place, and the stat names it.
    charge(resolved)
    try:
        os.stat(resolved)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise RuntimeError(f"Symlink loop from {exc.filename!r}") from exc
    return Path(resolved)


def _join_resolved(
    path: str, rest: str, seen: dict[str, str | None], charge: Any
) -> tuple[str, bool]:
    # posixpath._joinrealpath on 3.11 and 3.12, not strict, with each lstat charged first.
    if os.path.isabs(rest):
        rest = rest[1:]
        path = "/"
    while rest:
        name, _, rest = rest.partition("/")
        if not name or name == ".":
            continue
        if name == "..":
            if path:
                path, name = os.path.split(path)
                if name == "..":
                    path = os.path.join(path, "..", "..")
            else:
                path = ".."
            continue
        newpath = os.path.join(path, name)
        charge(newpath)
        try:
            is_link = stat.S_ISLNK(os.lstat(newpath).st_mode)
        except OSError:
            is_link = False
        if not is_link:
            path = newpath
            continue
        if newpath in seen:
            cached = seen[newpath]
            if cached is not None:
                path = cached
                continue
            # A link loop: what's resolved, and the rest as written.
            return os.path.join(newpath, rest), False
        seen[newpath] = None
        path, ok = _join_resolved(path, os.readlink(newpath), seen, charge)
        if not ok:
            return os.path.join(path, rest), False
        seen[newpath] = path
    return path, True


def _runtime_compose_services(
    root: Path,
    services: dict[str, Any],
    workdir: str,
    port: int,
    control: str,
    public_environment: dict[str, str],
    validate_environment_dependencies: bool = True,
    service_bases: dict[str, Path] | None = None,
    *,
    additional_services: list[str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Select a declared HTTP runtime and retain its submitted dependency graph."""
    component = (root / workdir).resolve()
    matches = []
    for name, service in services.items():
        if control:
            break
        if not isinstance(service, dict) or not service.get("build"):
            continue
        build = _interpolate_structure(service["build"], public_environment)
        context = build.get("context", ".") if isinstance(build, dict) else build
        if _resolved((service_bases or {}).get(name, root), str(context)) != component:
            continue
        ports = _interpolate_structure(service.get("ports") or [], public_environment)
        targets = [
            str(value.get("target", ""))
            if isinstance(value, dict)
            else str(value).split("/")[0].rsplit(":", 1)[-1]
            for value in ports
            if (
                value.get("protocol", "tcp") == "tcp"
                if isinstance(value, dict)
                else "/" not in str(value) or str(value).endswith("/tcp")
            )
        ]
        if str(port) in targets:
            matches.append(name)
    if not control and len(matches) != 1:
        raise RuntimeSelectionError(
            "control_service_ambiguous: declared runtime must match exactly one "
            f"Compose build context and container port; found {matches}"
        )
    control = control or matches[0]
    selected: set[str] = set()
    inactive_optional: dict[str, set[str]] = {}
    roots = {control, *(additional_services or [])}

    def profiles(service):
        values = _interpolate_structure(
            service.get("profiles") or [], public_environment
        )
        if (
            not isinstance(values, list)
            or len(values) > 32
            or any(
                not isinstance(value, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value)
                for value in values
            )
        ):
            raise RuntimeSelectionError("compose_profiles_invalid")
        return set(values)

    active_profiles = set(
        filter(None, public_environment.get("COMPOSE_PROFILES", "").split(","))
    )
    if len(active_profiles) > 32 or any(
        value != "*" and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value)
        for value in active_profiles
    ):
        raise RuntimeSelectionError("compose_profiles_invalid")
    for name in roots:
        if isinstance(services.get(name), dict):
            active_profiles.update(profiles(services[name]))
    pending = [control, *(additional_services or [])]
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        service = services.get(name)
        if not isinstance(service, dict):
            raise RuntimeSelectionError(f"compose_dependency_missing: {name}")
        for key in ("network_mode", "ipc", "pid"):
            # Compose takes a string here. Writing out anything else, as the check below does,
            # costs what an alias tree expands to: a few hundred bytes can reach gigabytes.
            if service.get(key) and not isinstance(service[key], str):
                raise RuntimeSelectionError(f"compose_invalid: {name} {key} must be a string")
        unsupported = [
            key
            for key in ("links", "volumes_from", "network_mode", "ipc", "pid")
            if service.get(key)
            and (
                key in {"links", "volumes_from"}
                or str(service[key]).startswith("service:")
            )
        ]
        if unsupported:
            raise RuntimeSelectionError(
                f"compose_dependency_unsupported: {name} uses {', '.join(unsupported)}"
            )
        dependencies = _interpolate_structure(
            service.get("depends_on") or [], public_environment
        )
        service["depends_on"] = dependencies
        if not isinstance(dependencies, (list, dict)) or not all(
            isinstance(dependency, str) for dependency in dependencies
        ):
            raise RuntimeSelectionError(f"compose_dependency_invalid: {name}")
        selected.add(name)
        for dependency in dependencies:
            options = dependencies[dependency] if isinstance(dependencies, dict) else {}
            target = services.get(dependency, {})
            dependency_profiles = profiles(target)
            if (
                isinstance(options, dict)
                and options.get("required") is False
                and dependency_profiles
                and not dependency_profiles.intersection(active_profiles)
                and "*" not in active_profiles
                and dependency not in roots
            ):
                inactive_optional.setdefault(name, set()).add(dependency)
                continue
            pending.append(dependency)
    for name in selected:
        dependencies = services[name].get("depends_on")
        if isinstance(dependencies, dict):
            services[name]["depends_on"] = {
                key: value for key, value in dependencies.items() if key in selected
            }
    omitted = set(services) - selected
    for name in selected if validate_environment_dependencies else ():
        environment = services[name].get("environment") or {}
        values = list(environment.values() if isinstance(environment, dict) else environment)
        # A value is a string, a number, a boolean or empty; anything else is an alias tree,
        # whose text would cost what it expands to.
        if any(isinstance(value, (list, dict)) for value in values):
            raise RuntimeSelectionError(f"compose_invalid: {name} environment values must be plain")
        if not isinstance(environment, dict):
            values = [str(item).partition("=")[2] for item in values]
        for value in values:
            try:
                value = _interpolate_structure(str(value or ""), public_environment)
            except RuntimeSelectionError:
                continue
            try:
                hostname = urlsplit(value if "://" in value else "//" + value).hostname
            except ValueError:
                continue
            if hostname in omitted and hostname not in inactive_optional.get(
                name, set()
            ):
                raise RuntimeSelectionError(
                    f"compose_dependency_undeclared: {name} environment references {hostname}; "
                    "declare it in depends_on"
                )
    try:
        order = tuple(
            TopologicalSorter(
                {
                    name: list(services[name].get("depends_on") or [])
                    for name in services
                    if name in selected
                }
            ).static_order()
        )
    except CycleError as exc:
        raise RuntimeSelectionError("compose_dependency_cycle") from exc
    return control, {name: services[name] for name in order}


# YAML aliases let a few hundred bytes stand for billions of nodes, and this copies each one.
_STRUCTURE_NODES = 100_000
# Linux's PATH_MAX.
_LONGEST_PATH = 4096
_LARGEST_STRING = 65536
_BRACE = re.compile(r"\$\{|\}")
_VARIABLE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")
_SELECTION_BUDGET: ContextVar[list[int] | None] = ContextVar("_SELECTION_BUDGET", default=None)


def _interpolate_structure(
    value: Any, public_environment: dict[str, str], budget: list[int] | None = None
) -> Any:
    if budget is None:
        budget = _SELECTION_BUDGET.get() or [_STRUCTURE_NODES]
    budget[0] -= 1
    if budget[0] < 0:
        raise RuntimeSelectionError("compose_structural_interpolation_limit")
    if isinstance(value, list):
        return [_interpolate_structure(item, public_environment, budget) for item in value]
    if isinstance(value, dict):
        return {
            _interpolate_structure(key, public_environment, budget): _interpolate_structure(
                item, public_environment, budget
            )
            for key, item in value.items()
        }
    if not isinstance(value, str):
        return value

    if len(value) > _LARGEST_STRING:
        raise RuntimeSelectionError("compose_structural_interpolation_limit")
    # A string is walked one character at a time, so its length is charged too.
    budget[0] -= len(value) // 100
    if budget[0] < 0:
        raise RuntimeSelectionError("compose_structural_interpolation_limit")
    result: list[str] = []
    # What the string builds, counted as it grows: a value from alk.yaml can be far longer than
    # its name.
    built = 0

    def add(piece: str) -> None:
        nonlocal built
        result.append(piece)
        built += len(piece)
        if built > _LARGEST_STRING:
            raise RuntimeSelectionError("compose_structural_interpolation_limit")

    def charge() -> None:
        # Each `$` and brace is worked in Python, so each is charged as a node is.
        budget[0] -= 1
        if budget[0] < 0:
            raise RuntimeSelectionError("compose_structural_interpolation_limit")

    position = 0
    while position < len(value):
        dollar = value.find("$", position)
        if dollar < 0:
            add(value[position:])
            break
        if dollar > position:
            add(value[position:dollar])
        position = dollar
        charge()
        if value.startswith("$$", position):
            add("$")
            position += 2
            continue
        if value.startswith("${", position):
            start = position + 2
            end, depth = start, 1
            while depth:
                brace = _BRACE.search(value, end)
                if brace is None:
                    break
                charge()
                if brace.group() == "${":
                    depth += 1
                    if depth > 32:
                        raise RuntimeSelectionError(
                            "compose_structural_interpolation_limit"
                        )
                else:
                    depth -= 1
                end = brace.end()
            if depth:
                raise RuntimeSelectionError(
                    "compose_structural_interpolation_unsupported"
                )
            expression = value[start : end - 1]
            match = re.fullmatch(
                r"([A-Za-z_][A-Za-z0-9_]*)(?:(:?[?+\-])(.*))?", expression, re.S
            )
            if not match:
                raise RuntimeSelectionError(
                    "compose_structural_interpolation_unsupported"
                )
            name, operator, default = match.groups()
            supplied = public_environment.get(name)
            present = supplied is not None and (
                bool(supplied) or not (operator or "").startswith(":")
            )
            if operator in {"+", ":+"}:
                replacement = (
                    _interpolate_structure(default, public_environment)
                    if present
                    else ""
                )
            elif present:
                replacement = supplied
            elif operator in {"-", ":-"}:
                replacement = _interpolate_structure(default, public_environment)
            else:
                raise RuntimeSelectionError(
                    f"compose_structural_configuration_missing: {name}"
                )
            add(replacement)
            position = end
        else:
            match = _VARIABLE.match(value, position)
            if not match:
                raise RuntimeSelectionError(
                    "compose_structural_interpolation_unsupported"
                )
            name = match.group(1)
            if name not in public_environment:
                raise RuntimeSelectionError(
                    f"compose_structural_configuration_missing: {name}"
                )
            add(public_environment[name])
            position = match.end()
    # Each alias of this string builds it again, so what it built is charged as its input is.
    budget[0] -= built // 100
    if budget[0] < 0:
        raise RuntimeSelectionError("compose_structural_interpolation_limit")
    return "".join(result)


# `<<` copies every key it merges into the mapping, so a small file can build millions of them.
_YAML_ENTRIES = 200_000
_YAML_INT = "tag:yaml.org,2002:int"
_YAML_MERGE = "tag:yaml.org,2002:merge"


class YamlTooLarge(yaml.YAMLError):
    pass


class _BoundedLoader(yaml.SafeLoader):
    # The scanner does work for every open `[` or `{` on every token, so deep nesting is slow
    # long before it builds anything. Real files nest three deep at most.
    def fetch_flow_collection_start(self, TokenClass):
        if self.flow_level >= 16:
            raise YamlTooLarge("yaml_too_deep")
        super().fetch_flow_collection_start(TokenClass)

    # Every tag that uses a `%TAG` handle gets its own copy of the prefix, so a long prefix
    # multiplies the file's size in memory. Real files don't use `%TAG`.
    def scan_tag_directive_prefix(self, start_mark):
        prefix = super().scan_tag_directive_prefix(start_mark)
        if len(prefix) > 1024:
            raise YamlTooLarge("yaml_tag_too_long")
        return prefix


def _merged_size(node: yaml.MappingNode, active: set[int], sizes: dict[int, int]) -> int:
    if id(node) in sizes:
        return sizes[id(node)]
    if id(node) in active or len(active) >= 32:
        raise YamlTooLarge("yaml_merge_cycle_or_depth")
    active.add(id(node))
    size = 0
    for key, value in node.value:
        if key.tag != _YAML_MERGE:
            size += 1
            continue
        for entry in value.value if isinstance(value, yaml.SequenceNode) else [value]:
            if isinstance(entry, yaml.MappingNode):
                size += _merged_size(entry, active, sizes)
    active.remove(id(node))
    sizes[id(node)] = size
    return size


def bounded_yaml_load(text: str, budget: list[int] | None = None) -> Any:
    """``yaml.safe_load``, refusing a document that would build too many entries, through merge
    keys or otherwise. ``budget`` carries one count across several files."""
    loader = _BoundedLoader(text)
    try:
        document = loader.get_single_node()
        if document is None:
            return None
        sizes: dict[int, int] = {}
        seen: set[int] = set()
        pending = [document]
        if budget is None:
            budget = [_YAML_ENTRIES]
        while pending:
            node = pending.pop()
            if id(node) in seen:
                continue
            seen.add(id(node))
            budget[0] -= 1
            if isinstance(node, yaml.MappingNode):
                budget[0] -= _merged_size(node, set(), sizes)
                pending.extend(part for pair in node.value for part in pair)
            elif isinstance(node, yaml.SequenceNode):
                pending.extend(node.value)
            # YAML 1.1 reads `1:2:3` as base 60, and building it costs the square of its parts.
            elif node.tag == _YAML_INT and node.value.count(":") > 16:
                raise YamlTooLarge("yaml_scalar_too_large")
            if budget[0] < 0:
                raise YamlTooLarge("yaml_too_large")
        return loader.construct_document(document)
    finally:
        loader.dispose()


def _load_compose(
    root: Path, compose: Path, environment: dict[str, str], compose_bytes: int
) -> tuple[dict[str, Any], dict[str, Path], tuple[Path, ...]]:
    seen = set()
    active = set()
    service_bases = {}
    total_bytes = 0
    entries = [_YAML_ENTRIES]

    def load(path):
        nonlocal total_bytes
        path = _charged_resolve(path)
        if not is_within(path, root) or not path.is_file():
            raise RuntimeSelectionError("compose_include_path_invalid")
        if path in active:
            raise RuntimeSelectionError("compose_include_cycle")
        if path in seen:
            raise RuntimeSelectionError("compose_include_duplicate")
        seen.add(path)
        active.add(path)
        if len(seen) > 32 or len(active) > 16:
            raise RuntimeSelectionError("compose_include_limit")
        total_bytes += path.stat().st_size
        if total_bytes > compose_bytes:
            raise RuntimeSelectionError("compose_include_limit")
        try:
            body = bounded_yaml_load(path.read_text(encoding="utf-8"), entries)
        except YamlTooLarge as exc:
            # The loader's reason, such as compose_yaml_too_deep, says what to look for.
            raise RuntimeSelectionError(f"compose_{exc}") from exc
        except (OSError, yaml.YAMLError) as exc:
            raise RuntimeSelectionError("compose_invalid") from exc
        if not isinstance(body, dict):
            raise RuntimeSelectionError("compose_invalid")
        includes = body.pop("include", [])
        if not isinstance(includes, list):
            raise RuntimeSelectionError("compose_include_invalid")
        for key in ("services", "volumes", "networks"):
            if key in body and not isinstance(body[key], dict):
                raise RuntimeSelectionError("compose_invalid")
        for name, service in body.get("services", {}).items():
            if not isinstance(service, dict):
                raise RuntimeSelectionError("compose_invalid")
            service_bases[name] = path.parent
        for include in includes:
            if not isinstance(include, str):
                raise RuntimeSelectionError("compose_include_unsupported")
            include = _interpolate_structure(include, environment)
            if (
                len(include) > _LONGEST_PATH
                or Path(include).is_absolute()
                or "://" in include
            ):
                raise RuntimeSelectionError("compose_include_path_invalid")
            child = load(path.parent / include)
            for key, value in child.items():
                if key in {"name", "version"} or key.startswith("x-"):
                    continue
                if key not in {"services", "volumes", "networks"}:
                    raise RuntimeSelectionError("compose_include_top_level_unsupported")
                target = body.setdefault(key, {})
                if target.keys() & value.keys():
                    raise RuntimeSelectionError(
                        f"compose_include_resource_conflict: {key}"
                    )
                target.update(value)
        active.remove(path)
        return body

    document = load(compose)
    return document, service_bases, tuple(sorted(seen))


def _rebase_service(service, base, destination, root, environment):
    def relative(value, *, interpolate=True):
        if interpolate:
            value = _interpolate_structure(value, environment)
        if (
            not isinstance(value, str)
            or Path(value).is_absolute()
            or value.startswith("~")
        ):
            raise RuntimeSelectionError("compose_source_path_invalid")
        path = _resolved(base, value)
        if not is_within(path, root):
            raise RuntimeSelectionError("compose_source_path_escape")
        return "./" + os.path.relpath(path, destination)

    build = service.get("build")
    if build:
        build = {"context": build} if isinstance(build, str) else build
        build["context"] = relative(build.get("context", "."), interpolate=False)
        service["build"] = build
    envfiles = service.get("env_file", [])
    envfiles = [envfiles] if isinstance(envfiles, str) else envfiles
    for index, entry in enumerate(envfiles):
        if isinstance(entry, str):
            envfiles[index] = relative(entry)
        elif isinstance(entry, dict) and "path" in entry:
            entry["path"] = relative(entry["path"])
        else:
            raise RuntimeSelectionError("compose_env_file_invalid")
    if envfiles:
        service["env_file"] = envfiles
    mounts = service.get("volumes", [])
    for index, mount in enumerate(mounts):
        if isinstance(mount, str):
            parts = mount.split(":")
            if parts[0].startswith((".", "/", "~")):
                parts[0] = relative(parts[0], interpolate=False)
                mounts[index] = ":".join(parts)
        elif isinstance(mount, dict) and mount.get("type") == "bind":
            mount["source"] = relative(mount["source"], interpolate=False)
