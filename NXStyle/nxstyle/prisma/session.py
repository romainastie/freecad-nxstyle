# SPDX-License-Identifier: LGPL-2.1-or-later
"""One root operation with nested, uncommitted value constructors."""

from copy import deepcopy
from dataclasses import dataclass
from .definitions import PreviewResult, as_spec


@dataclass
class ConstructorFrame:
    kind: str
    spec: dict
    on_accept: object = None


class CommandSession:
    """Preview is read-only; the adapter atomically commits each OK or Apply.

    A child constructor never opens a document transaction. Canceling it leaves
    the root recipe intact. Apply commits one operation and keeps the recipe open.
    """

    def __init__(self, adapter):
        self.adapter = adapter
        self.open = False
        self.root = None
        self.editing = None
        self.stack = []
        self.preview = PreviewResult(False, error="No active command")
        self.committed = []
        self.error = ""

    @property
    def active(self):
        return self.open

    @property
    def current(self):
        return self.stack[-1].spec if self.stack else self.root

    def begin(self, spec=None, editing=None):
        if self.open:
            raise RuntimeError("A command is already active")
        self.editing = editing
        self.root = as_spec(self.adapter.load(editing) if spec is None and editing is not None else spec)
        if hasattr(self.adapter, "begin_context"):
            self.adapter.begin_context(self.root, editing)
        self.open = True
        self.stack = []
        self.error = ""
        return self.update(self.root)

    def update(self, spec):
        self._require_open()
        spec = as_spec(spec)
        if self.stack:
            self.stack[-1].spec = spec
        else:
            self.root = spec
        try:
            result = self.adapter.preview(deepcopy(spec))
            if not isinstance(result, PreviewResult):
                raise TypeError("Adapter.preview must return PreviewResult")
        except Exception as exc:
            result = PreviewResult(False, error=str(exc))
        self.preview = result
        self.error = result.error
        return result

    def push(self, kind, initial_spec, on_accept=None):
        self._require_open()
        self.stack.append(ConstructorFrame(kind, as_spec(initial_spec), on_accept))
        return self.update(initial_spec)

    def pop(self, value=None, accepted=True):
        self._require_open()
        if not self.stack:
            raise RuntimeError("No child constructor is open")
        child = self.stack[-1]
        if accepted:
            candidate = as_spec(value) if value is not None else child.spec
            result = self.adapter.preview(deepcopy(candidate))
            if not result.valid:
                self.preview = result
                self.error = result.error
                raise ValueError(result.error or "The constructor is incomplete")
        self.stack.pop()
        if accepted and child.on_accept is not None:
            child.on_accept(deepcopy(candidate))
        self.update(self.current)
        return deepcopy(candidate) if accepted else None

    def _commit(self):
        self._require_open()
        if self.stack:
            raise RuntimeError("Finish the active constructor first")
        result = self.update(self.root)
        if not result.valid:
            raise ValueError(result.error or "The command is incomplete")
        try:
            feature = self.adapter.commit(deepcopy(self.root), editing=self.editing)
        except Exception as exc:
            self.error = str(exc)
            self.preview = PreviewResult(False, error=self.error)
            raise
        self.committed.append(feature)
        self.error = ""
        return feature

    def apply(self):
        feature = self._commit()
        # Applying an edit continues editing the same feature; creating continues
        # with another independent operation, matching the primitive Apply flow.
        if self.editing is not None:
            self.editing = feature
        self.update(self.root)
        return feature

    def accept(self):
        feature = self._commit()
        self.open = False
        self.stack = []
        return feature

    def cancel(self):
        if not self.open:
            return False
        self.stack = []
        self.root = None
        self.editing = None
        self.open = False
        self.preview = PreviewResult(False, error="Command cancelled")
        self.error = ""
        return True

    def _require_open(self):
        if not self.open:
            raise RuntimeError("No active command")
