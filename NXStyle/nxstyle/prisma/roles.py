# SPDX-License-Identifier: LGPL-2.1-or-later
"""Inert, deliberately bounded one-way NX role layout importer.

No action, program, environment variable, bitmap, or file named inside a role
is executed or opened. Inheritance is resolved only from explicitly supplied
documents. The shipped layout remains usable without any NX file.
"""

from copy import deepcopy
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

MAX_ROLE_BYTES = 4 * 1024 * 1024
MAX_ROLE_NODES = 20000
MAX_ROLE_DEPTH = 32
SOLID_COMMANDS = {"Prisma_Block", "Prisma_Cylinder", "Prisma_Unite", "Prisma_Subtract", "Prisma_Intersect"}
# This is an explicitly bounded prototype alias table, not a verified inventory
# of all NX 1953 command identifiers. Unknown IDs remain disabled placeholders.
COMMAND_REGISTRY_VERSION = "prisma-nx-observed-v1"
COMMAND_REGISTRY = {}
for _label in ("Point", "Axis", "Plane", "Block", "Cylinder", "Unite", "Subtract", "Intersect"):
    _command = "Prisma_" + ("Datum" + _label if _label in ("Axis", "Plane") else _label)
    for _alias in (_command, _label.upper() + "_FEATURE", "UG_MODELING_" + _label.upper() + "_FEATURE",
                   "UG_MODELING_" + _label.upper()):
        COMMAND_REGISTRY[_alias] = {"command": _command, "label": _label, "enabled": True}

DEFAULT_LAYOUT = {
    "version": 1, "name": "PRISMA Basic Modeling", "source": "independently-authored",
    "registry_version": COMMAND_REGISTRY_VERSION,
    "tabs": [{"id": "home", "name": "Home", "groups": [
        {"id": "construction", "name": "Datum/Point", "items": [
            {"cmd": "Prisma_Point", "label": "Point"}, {"cmd": "Prisma_DatumAxis", "label": "Datum Axis"},
            {"cmd": "Prisma_DatumPlane", "label": "Datum Plane"}]},
        {"id": "solids", "name": "Design Feature", "items": [
            {"cmd": "Prisma_Block", "label": "Block"}, {"cmd": "Prisma_Cylinder", "label": "Cylinder"}]},
        {"id": "combine", "name": "Combine", "items": [
            {"cmd": "Prisma_Unite", "label": "Unite"}, {"cmd": "Prisma_Subtract", "label": "Subtract"},
            {"cmd": "Prisma_Intersect", "label": "Intersect"}]}]}],
    "shortcuts": [], "radials": [],
}


@dataclass
class RoleImport:
    layout: dict
    diagnostics: list = field(default_factory=list)
    unknown_commands: list = field(default_factory=list)
    source_name: str = ""

    @property
    def complete(self):
        return not self.diagnostics and not self.unknown_commands


def default_layout():
    return deepcopy(DEFAULT_LAYOUT)


def _xml(content):
    if isinstance(content, str):
        content = content.encode("utf-8")
    if not isinstance(content, bytes):
        raise TypeError("A role must be XML text or bytes")
    if len(content) > MAX_ROLE_BYTES:
        raise ValueError("The role exceeds the 4 MB import limit")
    # ElementTree does not retrieve external resources; reject declarations too,
    # including internal entity expansion and alternate byte-encoded XML.
    if b"\x00" in content or re.search(br"<!\s*(DOCTYPE|ENTITY)", content, re.I):
        raise ValueError("DTD and entity declarations are not supported in roles")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValueError(f"Invalid NX role XML: {exc}") from exc
    if root.tag.split("}")[-1] != "NX_PROFILES":
        raise ValueError("Expected an NX_PROFILES document")
    pending = [(root, 0)]
    count = 0
    while pending:
        node, depth = pending.pop()
        count += 1
        if count > MAX_ROLE_NODES or depth > MAX_ROLE_DEPTH:
            raise ValueError("The role exceeds the XML complexity limit")
        pending.extend((child, depth + 1) for child in node)
    return root


def _safe_reference(name):
    return not (":" in name or "\\" in name or name.startswith("/")
                or ".." in name.split("/") or "\x00" in name)


def required_role_references(content, inherited=None):
    """List unresolved safe refRole names without opening any referenced path."""
    inherited = inherited or {}
    missing, active = [], set()
    def visit(value, identity):
        if len(active) >= 16 or identity in active:
            raise ValueError("Role inheritance contains a cycle or exceeds the depth limit")
        active.add(identity)
        parent = _xml(value).get("refRole")
        if parent and _safe_reference(parent):
            if parent in inherited:
                visit(inherited[parent], parent)
            elif parent not in missing:
                missing.append(parent)
        active.remove(identity)
    visit(content, "<selected role>")
    return missing


def selected_role_sources(paths, allowed_roots):
    """Read only explicitly picked files inside explicitly selected root folders."""
    roots = [Path(root).resolve() for root in allowed_roots]
    sources = {}
    for value in paths:
        path = Path(value).resolve()
        if not any(path.is_relative_to(root) for root in roots):
            raise ValueError("The selected inherited role is outside the selected role roots")
        if path.stat().st_size > MAX_ROLE_BYTES:
            raise ValueError("The role exceeds the 4 MB import limit")
        content = path.read_bytes()
        _xml(content)
        names = {path.name}
        names.update(path.relative_to(root).as_posix() for root in roots if path.is_relative_to(root))
        for name in names:
            if name in sources and sources[name] != content:
                raise ValueError(f"Two selected roles have the same identifier {name!r}")
            sources[name] = content
    return sources


def normalize_shortcut(value):
    """Normalize the documented textual subset; never interpret key scripts."""
    if not isinstance(value, str) or len(value) > 80:
        raise ValueError("Invalid shortcut")
    parts = [part.strip() for part in value.replace("-", "+").split("+")]
    modifiers, key = set(), None
    aliases = {"control": "Ctrl", "ctrl": "Ctrl", "shift": "Shift", "alt": "Alt", "meta": "Meta", "cmd": "Meta"}
    special = {name.casefold(): name for name in ("Space", "Tab", "Escape", "Delete", "Backspace", "Insert",
               "Home", "End", "Left", "Right", "Up", "Down", "PageUp", "PageDown", "Return", "Enter")}
    for part in parts:
        if part.casefold() in aliases:
            modifiers.add(aliases[part.casefold()])
        elif key is None and (len(part) == 1 and part.isalnum() or re.fullmatch(r"F(?:[1-9]|[12][0-9]|3[0-5])", part, re.I)):
            key = part.upper()
        elif key is None and part.casefold() in special:
            key = special[part.casefold()]
        else:
            raise ValueError(f"Unsupported shortcut syntax {value!r}")
    if key is None:
        raise ValueError("A shortcut needs a key")
    return "+".join([modifier for modifier in ("Ctrl", "Alt", "Shift", "Meta") if modifier in modifiers] + [key])


def import_role(content, *, registry=None, registry_version=None, inherited=None, baseline=None, source_name="role.mtx"):
    """Return a previewable layout; unknown commands keep their original slots.

    ``inherited`` maps exact refRole identifiers to supplied XML bytes/text.
    Filesystem traversal and automatic NX installation discovery are prohibited.
    Unspecified baseline content comes from our own default preset.
    """
    version = registry_version or (COMMAND_REGISTRY_VERSION if registry is None else "custom-unversioned")
    registry = registry if registry is not None else COMMAND_REGISTRY
    inherited = inherited or {}
    result = RoleImport(deepcopy(baseline) if baseline is not None else default_layout(), source_name=source_name)
    result.layout["source"] = "nx-role-prototype"
    result.layout["registry_version"] = version
    visited = set()

    def mapped(node):
        external = node.get("name") or node.get("id") or ""
        hit = registry.get(external)
        if isinstance(hit, str):
            hit = {"command": hit, "label": external, "enabled": True}
        if hit:
            return {"cmd": hit.get("command", hit.get("cmd")), "label": node.get("label") or hit.get("label", external),
                    "enabled": bool(hit.get("enabled", True)), "external_id": external}
        if external not in result.unknown_commands:
            result.unknown_commands.append(external)
        return {"cmd": None, "label": node.get("label") or external or "Unknown command", "enabled": False,
                "external_id": external, "diagnostic": "This NX command is not implemented"}

    def consume(xml_content, identity):
        if len(visited) >= 16:
            raise ValueError("Role inheritance exceeds the depth limit")
        if identity in visited:
            raise ValueError("Role inheritance contains a cycle")
        visited.add(identity)
        root = _xml(xml_content)
        parent = root.get("refRole")
        if parent:
            if not _safe_reference(parent):
                result.diagnostics.append(f"Unsafe inherited role identifier {parent!r} was ignored")
            elif parent in inherited:
                consume(inherited[parent], parent)
            else:
                result.diagnostics.append(f"Inherited baseline {parent!r} was not supplied; PRISMA's default is used")
        title = root.get("title")
        if title:
            result.layout["name"] = title
        for node in root.iter():
            tag = node.tag.split("}")[-1].lower()
            sequence = next((node.get(key) for key in ("key", "keys", "shortcut", "keySequence", "accelerator")
                             if node.get(key)), None)
            if not sequence or tag not in ("shortcut", "accelerator", "actionitem", "action"):
                continue
            external = node.get("command") or node.get("action") or node.get("name") or node.get("id") or ""
            proxy = ET.Element("ActionItem", name=external, label=node.get("label", ""))
            item = mapped(proxy)
            try:
                item["key"] = normalize_shortcut(sequence)
            except ValueError as exc:
                result.diagnostics.append(str(exc))
                continue
            conflicts = [old for old in result.layout.setdefault("shortcuts", []) if old.get("key") == item["key"]]
            if conflicts and any(old.get("cmd") != item.get("cmd") for old in conflicts):
                message = f"Shortcut {item['key']} conflicts between imported commands; bindings are disabled"
                result.diagnostics.append(message)
                for old in conflicts:
                    old.update(enabled=False, diagnostic=message)
                item.update(enabled=False, diagnostic=message)
            if not conflicts or not all(old.get("cmd") == item.get("cmd") for old in conflicts):
                result.layout["shortcuts"].append(item)
        profiles = [n for n in root if n.tag.split("}")[-1] == "Profile"]
        for profile in profiles:
            app = profile.get("name", "")
            if app not in ("", "UG_APP_MODELING", "UG_APP_NOPART", "UG_APP_GATEWAY"):
                result.diagnostics.append(f"Application {app!r} is outside the prototype import subset")
                continue
            for node in profile.iter():
                if node.tag.split("}")[-1] != "ActionList":
                    continue
                items = [mapped(item) for item in node if item.tag.split("}")[-1] in ("ActionItem", "Action")]
                if not items:
                    continue
                identifier = node.get("name", "Imported")
                kind = node.get("type", "group").lower()
                container = {"id": identifier, "name": node.get("title") or identifier, "items": items,
                             "application": app}
                if "radial" in kind or "popup" in kind:
                    result.layout.setdefault("radials", []).append(container)
                else:
                    # ActionLists whose baselines are unavailable remain explicit
                    # groups, rather than inventing a ribbon ancestry.
                    groups = result.layout["tabs"][0]["groups"]
                    old = next((i for i, g in enumerate(groups) if g.get("id") == identifier), None)
                    if old is None:
                        groups.append(container)
                    else:
                        groups[old] = container
        ignored = {node.tag.split("}")[-1] for node in root.iter()
                   if node.tag.split("}")[-1] in ("Preferences", "SessionSettings", "Repository")}
        if ignored:
            result.diagnostics.append("Not imported: " + ", ".join(sorted(ignored)))
        if any(node.get("action") or node.get("ACTIONS") for node in root.iter()):
            result.diagnostics.append("Executable action attributes were ignored")
        visited.remove(identity)

    consume(content, source_name)
    result.layout["unknown_commands"] = list(result.unknown_commands)
    result.diagnostics.append("Prototype MTX subset: full NX baseline/layout fidelity is not verified")
    return result


def read_role(path, **kwargs):
    path = Path(path)
    if path.stat().st_size > MAX_ROLE_BYTES:
        raise ValueError("The role exceeds the 4 MB import limit")
    return import_role(path.read_bytes(), source_name=path.name, **kwargs)


def should_offer_role_import(preferences, available_commands):
    """Offer once after all five solid commands are available; never gate startup."""
    if preferences.get("role_import_prompt_seen") or preferences.get("role_imported"):
        return False
    return SOLID_COMMANDS.issubset(set(available_commands))


def dismiss_role_import(preferences):
    preferences["role_import_prompt_seen"] = True


def save_layout(path, layout):
    """Write our own normalized JSON profile, never the source MTX."""
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(layout, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)
