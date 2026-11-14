# SPDX-License-Identifier: LGPL-2.1-or-later
"""Modeless command dialogs and reusable, uncommitted value constructors.

The dialog never creates geometry. A CommandSession owns one root recipe and a
stack of value constructors; the model adapter validates previews and commits.
"""

from copy import deepcopy

import FreeCAD as App
import FreeCADGui as Gui

from ..qt import QtCore, QtGui, QtWidgets, Qt, exec_menu
from .catalog import TITLES, KIND_COMMANDS, METHOD_ORDER, METHOD_TEXT
from .definitions import FIELD_SCHEMAS, METHODS, METHOD_LABELS, PreviewResult, command_spec
from .session import CommandSession
from .preview import Preview, xyz

_controller = None


def current():
    return _controller if _controller is not None and _controller.session.active else None


def controller():
    global _controller
    if _controller is None:
        _controller = Controller()
    return _controller


def launch(kind, editing=None):
    return controller().begin(kind, editing=editing)


def reference(doc, obj, sub="", point=()):
    return {"document": str(doc), "object": str(obj), "subelement": str(sub or ""), "point": list(point)}


def reference_identity(ref):
    return tuple(ref.get(key, "") for key in ("document", "object", "subelement"))


def workplane_point(view, x, y, frame=None):
    """Intersect the screen ray with WCS XY, including perspective views."""
    origin = xyz(view.getPoint(x, y))
    direction = xyz(view.getViewDirection()) if hasattr(view, "getViewDirection") else (0., 0., -1.)
    try:
        camera = view.getCameraNode()
        if "Perspective" in str(camera.getTypeId().getName()):
            eye = xyz(camera.position.getValue())
            direction = tuple(origin[i] - eye[i] for i in range(3))
            origin = eye
    except (AttributeError, RuntimeError):
        pass
    base = xyz(frame.Base) if frame is not None else (0., 0., 0.)
    normal = xyz(frame.Rotation.multVec(App.Vector(0, 0, 1))) if frame is not None else (0., 0., 1.)
    denominator = sum(normal[i] * direction[i] for i in range(3))
    magnitude = sum(c*c for c in direction) ** .5
    if magnitude < 1e-12 or abs(denominator) < 1e-10 * magnitude:
        raise ValueError("The cursor ray is parallel to the WCS plane; rotate the view or specify coordinates")
    distance = sum(normal[i] * (base[i] - origin[i]) for i in range(3)) / denominator
    return tuple(origin[i] + distance * direction[i] for i in range(3))


def mark_points(spec, specified):
    for value in spec.values():
        if isinstance(value, dict) and value.get("kind"):
            if value["kind"] == "point":
                value.setdefault("_specified", specified)
            mark_points(value, specified)


class Collector(QtWidgets.QWidget):
    activated = QtCore.Signal(str)
    construct = QtCore.Signal(str)
    cleared = QtCore.Signal(str)

    def __init__(self, definition, parent=None):
        super().__init__(parent)
        self.definition = definition
        self.setObjectName("PrismaCollector")
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        self.pick = QtWidgets.QPushButton(definition.label)
        self.pick.setObjectName("collector_" + definition.name)
        self.pick.setCheckable(True)
        self.pick.setToolTip("Select geometry in the graphics window or Part Navigator")
        self.pick.clicked.connect(lambda: self.activated.emit(definition.name))
        layout.addWidget(self.pick, 1)
        self.required = QtWidgets.QLabel("*")
        self.required.setMinimumWidth(10)
        layout.addWidget(self.required)
        self.count = QtWidgets.QLabel("(0)")
        self.count.setMinimumWidth(28)
        layout.addWidget(self.count)
        if definition.kind in ("point", "vector"):
            self.more = QtWidgets.QToolButton()
            self.more.setText("…")
            self.more.setToolTip(TITLES[definition.kind] + " Constructor")
            self.more.setAccessibleName(definition.label + " Constructor")
            self.more.clicked.connect(lambda: self.construct.emit(definition.name))
            layout.addWidget(self.more)
        clear = QtWidgets.QToolButton()
        clear.setText("×")
        clear.setToolTip("Clear selection")
        clear.setAccessibleName("Clear " + definition.label)
        clear.clicked.connect(lambda: self.cleared.emit(definition.name))
        layout.addWidget(clear)

    def refresh(self, spec, active=False):
        definition = self.definition
        if definition.kind in ("point", "vector"):
            value = spec.get(definition.name, {})
            count = int(value.get("_specified", definition.kind == "vector"))
            tooltip = METHOD_LABELS.get(value.get("method"), "")
            if value.get("fields"):
                tooltip += "\n" + ", ".join(str(v) for v in value["fields"].values())
        else:
            refs = spec.get("references", {}).get(definition.name, [])
            count = len(refs)
            tooltip = "\n".join(r.get("object", "") + ("." + r["subelement"] if r.get("subelement") else "")
                                for r in refs)
        minimum = 2 if definition.name == "objects" and spec["method"] != "curves_points" else 1
        complete = count >= minimum
        self.count.setText("(%d)" % count)
        self.count.setToolTip(tooltip)
        self.pick.setChecked(active)
        self.required.setText("✓" if complete else "*")
        self.required.setStyleSheet("color: %s; font-weight: bold" % ("#348c39" if complete else "#c52c1f"))
        self.required.setAccessibleName("Complete" if complete else "Required")
        self.pick.setStyleSheet("QPushButton { text-align: left; padding: 5px; background: %s; color: #222; border: 1px solid #aeb8bf; }"
                               % ("#fff0a1" if active else "#edf5eb" if complete else "#f4f5f6"))
        self.setProperty("active", active)
        self.setProperty("complete", count > 0)
        self.setStyleSheet("QWidget#PrismaCollector { background: %s; border: 1px solid %s; border-radius: 2px; }"
                           % ("#fff0a1" if active else "#edf5eb" if count else "#f4f5f6",
                              "#c5a526" if active else "#c9d0d5"))


class CommandDialog(QtWidgets.QDialog):
    def __init__(self, owner, spec, nested=False):
        super().__init__(Gui.getMainWindow(), Qt.Tool)
        self.owner, self.spec, self.nested = owner, spec, nested
        self.collectors, self.editors = {}, {}
        self.active_collector = None
        self._building = True
        self.setObjectName("PrismaCommandDialog")
        self.setStyleSheet("QDialog#PrismaCommandDialog { background: #f2f5f7; color: #243447; }"
                           "QDialog#PrismaCommandDialog QLabel, QDialog#PrismaCommandDialog QCheckBox { color: #243447; }"
                           "QDialog#PrismaCommandDialog QLineEdit, QDialog#PrismaCommandDialog QComboBox {"
                           "background: white; color: #243447; border: 1px solid #b6c3cc; padding: 3px; }"
                           "QDialog#PrismaCommandDialog QGroupBox { color: #243447; }")
        self.setWindowTitle(TITLES.get(spec["kind"], spec["kind"].title()) + (" Constructor" if nested else ""))
        self.setMinimumWidth(345)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setWindowModality(Qt.NonModal)
        self.outer = QtWidgets.QVBoxLayout(self)
        self.outer.setContentsMargins(10, 9, 10, 9)
        method_row = QtWidgets.QHBoxLayout()
        method_row.addWidget(QtWidgets.QLabel("Type"))
        self.method = QtWidgets.QComboBox()
        self.method.setObjectName("construction_method")
        declared = METHODS[spec["kind"]]
        ordered = list(METHOD_ORDER.get(spec["kind"], declared))
        ordered.extend(method for method in declared if method not in ordered)
        for method in ordered:
            if method not in declared or (spec["kind"] == "plane" and method == "three_points"):
                continue
            label = METHOD_TEXT.get(spec["kind"], {}).get(method, METHOD_LABELS.get(method, method.replace("_", " ").title()))
            self.method.addItem(label, method)
        displayed_method = "curves_points" if spec["kind"] == "plane" and spec["method"] == "three_points" else spec["method"]
        self.method.setCurrentIndex(self.method.findData(displayed_method))
        self.method.currentIndexChanged.connect(self.method_changed)
        method_row.addWidget(self.method, 1)
        self.outer.addLayout(method_row)
        if spec["kind"] == "plane" and spec["method"] in ("curves_points", "three_points"):
            row = QtWidgets.QHBoxLayout()
            row.addWidget(QtWidgets.QLabel("Definition"))
            self.curves_mode = QtWidgets.QComboBox()
            self.curves_mode.setObjectName("curves_points_definition")
            self.curves_mode.addItem("Select Curves and Points", "curves_points")
            self.curves_mode.addItem("Three Points", "three_points")
            self.curves_mode.setCurrentIndex(self.curves_mode.findData(spec["method"]))
            self.curves_mode.currentIndexChanged.connect(self.curves_mode_changed)
            row.addWidget(self.curves_mode, 1)
            self.outer.addLayout(row)
        self.body = QtWidgets.QWidget()
        self.form = QtWidgets.QVBoxLayout(self.body)
        self.form.setContentsMargins(0, 0, 0, 0)
        self.outer.addWidget(self.body)
        if spec["kind"] == "point" and spec["method"] in ("coordinates", "cursor", "point_expression"):
            row = QtWidgets.QHBoxLayout()
            row.addWidget(QtWidgets.QLabel("Coordinates"))
            self.coordinate_mode = QtWidgets.QComboBox()
            self.coordinate_mode.setObjectName("coordinate_mode")
            for label, value in (("Cartesian", "cartesian"), ("Cylindrical", "cylindrical"), ("Spherical", "spherical")):
                self.coordinate_mode.addItem(label, value)
            self.coordinate_mode.setCurrentIndex(max(0, self.coordinate_mode.findData(spec.get("coordinate_mode", "cartesian"))))
            self.coordinate_mode.currentIndexChanged.connect(self.coordinates_changed)
            row.addWidget(self.coordinate_mode, 1)
            self.form.addLayout(row)
        self._build_fields()
        if spec["kind"] == "point":
            self._build_offset()
        frame_row = QtWidgets.QHBoxLayout()
        frame_row.addWidget(QtWidgets.QLabel("Reference"))
        self.frame = QtWidgets.QComboBox()
        self.frame.setObjectName("reference_frame")
        self.frame.addItem("Absolute - Work Part", "absolute")
        self.frame.addItem("WCS", "wcs")
        self.frame.setCurrentIndex(self.frame.findData(spec.get("frame", "absolute")))
        self.frame.currentIndexChanged.connect(lambda: self.option("frame", self.frame.currentData()))
        frame_row.addWidget(self.frame, 1)
        self.outer.addLayout(frame_row)
        self.associative_wcs = QtWidgets.QCheckBox("Associative WCS")
        self.associative_wcs.setObjectName("associative_wcs")
        self.associative_wcs.setChecked(spec.get("associative_frame", False))
        self.associative_wcs.setEnabled(spec.get("frame", "absolute") == "wcs")
        self.associative_wcs.setToolTip("Follow later changes to the named WCS; clear to capture its current frame")
        self.associative_wcs.toggled.connect(lambda value: self.option("associative_frame", value))
        self.outer.addWidget(self.associative_wcs)
        self.associative = QtWidgets.QCheckBox("Associative")
        self.associative.setObjectName("associative")
        self.associative.setChecked(spec.get("associative", True))
        self.associative.setToolTip("Keep references and expressions linked to source geometry; clear for a fixed result")
        self.associative.toggled.connect(lambda value: self.option("associative", value))
        self.outer.addWidget(self.associative)
        if spec["kind"] in ("vector", "axis", "plane", "cylinder"):
            reverse = QtWidgets.QCheckBox("Reverse Direction")
            reverse.setObjectName("reverse_direction")
            reverse.setChecked(spec.get("reverse", False))
            reverse.toggled.connect(lambda value: self.option("reverse", value))
            self.outer.addWidget(reverse)
        self.preview = QtWidgets.QCheckBox("Preview")
        self.preview.setChecked(True)
        self.preview.toggled.connect(owner.show_preview)
        preview_row = QtWidgets.QHBoxLayout()
        preview_row.addWidget(self.preview)
        preview_row.addStretch(1)
        self.show_result = QtWidgets.QPushButton("Show Result")
        self.show_result.setObjectName("show_result")
        self.show_result.setAutoDefault(False)
        self.show_result.clicked.connect(owner.show_result)
        preview_row.addWidget(self.show_result)
        self.outer.addLayout(preview_row)
        self.diagnostic = QtWidgets.QLabel()
        self.diagnostic.setObjectName("command_diagnostic")
        self.diagnostic.setWordWrap(True)
        self.diagnostic.setMinimumHeight(32)
        self.outer.addWidget(self.diagnostic)
        # QDialogButtonBox reorders these on macOS; NX keeps OK, Apply, Cancel.
        self.buttons = QtWidgets.QWidget()
        button_layout = QtWidgets.QHBoxLayout(self.buttons)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.addStretch(1)
        self.ok = QtWidgets.QPushButton("OK")
        button_layout.addWidget(self.ok)
        self.ok.setObjectName("command_ok")
        self.ok.clicked.connect(owner.accept)
        self.apply = QtWidgets.QPushButton("Apply")
        button_layout.addWidget(self.apply)
        self.apply.setObjectName("command_apply")
        self.apply.setVisible(not nested)
        self.apply.clicked.connect(owner.apply)
        self.cancel = QtWidgets.QPushButton("Cancel")
        button_layout.addWidget(self.cancel)
        for button in (self.ok, self.apply, self.cancel):
            button.setMinimumWidth(75)
        self.cancel.clicked.connect(owner.cancel_current)
        # Return in an expression commits that field, never the whole feature.
        self.ok.setAutoDefault(False)
        self.ok.setDefault(False)
        self.apply.setAutoDefault(False)
        self.cancel.setAutoDefault(False)
        self.outer.addWidget(self.buttons)
        hint = QtWidgets.QLabel("MB2: finish step / OK    Ctrl+MB2: Apply    MB3: options")
        hint.setStyleSheet("color: #64717b; font-size: 10px")
        self.outer.addWidget(hint)
        self._building = False
        self.coordinates_changed()
        self.activate_next()

    def _build_fields(self):
        for definition in FIELD_SCHEMAS[self.spec["kind"]][self.spec["method"]]:
            name = definition.name
            if definition.kind in ("point", "vector", "reference", "body"):
                widget = Collector(definition)
                widget.activated.connect(self.activate_collector)
                widget.construct.connect(self.owner.open_constructor)
                widget.cleared.connect(self.clear_collector)
                self.collectors[name] = widget
                self.form.addWidget(widget)
            else:
                row = QtWidgets.QHBoxLayout()
                label = QtWidgets.QLabel(definition.label)
                label.setObjectName("label_" + name)
                label.setMinimumWidth(115)
                row.addWidget(label)
                if definition.kind == "choice":
                    widget = QtWidgets.QComboBox()
                    choices = ("ArcLengthPercent", "ArcLength", "Parameter")
                    for value in choices:
                        widget.addItem({"ArcLengthPercent": "% Arc Length", "ArcLength": "Arc Length",
                                        "Parameter": "Parameter"}[value], value)
                    widget.setCurrentIndex(max(0, widget.findData(self.spec["fields"].get(name))))
                    widget.currentIndexChanged.connect(lambda _=0, n=name, w=widget: self.field(n, w.currentData()))
                else:
                    widget = QtWidgets.QLineEdit(str(self.spec["fields"].get(name, definition.default)))
                    widget.setToolTip("A number, unit expression, or expression referring to a model parameter")
                    widget.textChanged.connect(lambda value, n=name: self.field(n, value))
                widget.setObjectName("field_" + name)
                self.editors[name] = widget
                row.addWidget(widget, 1)
                self.form.addLayout(row)
        if self.spec["kind"] in ("block", "cylinder"):
            self.boolean = QtWidgets.QComboBox()
            self.boolean.setObjectName("boolean_operation")
            for label, value in (("None", "none"), ("Unite", "unite"), ("Subtract", "subtract"), ("Intersect", "intersect")):
                self.boolean.addItem(label, value)
            self.boolean.setCurrentIndex(max(0, self.boolean.findData(self.spec.get("boolean", "none"))))
            self.boolean.currentIndexChanged.connect(self.boolean_changed)
            row = QtWidgets.QHBoxLayout()
            row.addWidget(QtWidgets.QLabel("Boolean"))
            row.addWidget(self.boolean, 1)
            self.form.addLayout(row)
            from .definitions import FieldDef
            target = Collector(FieldDef("target", "Target Body", "body", []))
            target.activated.connect(self.activate_collector)
            target.cleared.connect(self.clear_collector)
            self.collectors["target"] = target
            self.form.addWidget(target)
            target.setVisible(self.spec.get("boolean", "none") != "none")
        if self.spec["kind"] in ("block", "cylinder", "unite", "subtract", "intersect"):
            self.keep_target = QtWidgets.QCheckBox("Keep Target")
            self.keep_tool = QtWidgets.QCheckBox("Keep Tool")
            for key, box in (("keep_target", self.keep_target), ("keep_tool", self.keep_tool)):
                box.setObjectName(key)
                box.setChecked(self.spec.get(key, False))
                box.toggled.connect(lambda value, k=key: self.option(k, value))
                self.form.addWidget(box)
            self._boolean_visibility()

    def field(self, name, value):
        self.spec["fields"][name] = value
        self.changed()

    def coordinates_changed(self):
        if not hasattr(self, "coordinate_mode"):
            return
        mode = self.coordinate_mode.currentData()
        self.spec["coordinate_mode"] = mode
        labels = {"cartesian": ("XC", "YC", "ZC"), "cylindrical": ("Radius", "Angle", "ZC"),
                  "spherical": ("Radius", "Azimuth", "Elevation")}[mode]
        for name, text in zip(("x", "y", "z"), labels):
            self.body.findChild(QtWidgets.QLabel, "label_" + name).setText(text)
        self.changed()

    def _build_offset(self):
        group = QtWidgets.QGroupBox("Offset")
        form = QtWidgets.QFormLayout(group)
        self.offset_mode = QtWidgets.QComboBox()
        self.offset_mode.setObjectName("offset_mode")
        for label, value in (("None", "none"), ("Rectangular", "rectangular"),
                             ("Cylindrical", "cylindrical"), ("Spherical", "spherical")):
            self.offset_mode.addItem(label, value)
        offset = self.spec.setdefault("offset", {"method": "none", "values": []})
        self.offset_mode.setCurrentIndex(max(0, self.offset_mode.findData(offset.get("method", "none"))))
        form.addRow("Type", self.offset_mode)
        self.offset_labels, self.offset_fields = [], []
        values = list(offset.get("values", [])) + ["0", "0", "0"]
        for index in range(3):
            label = QtWidgets.QLabel()
            field = QtWidgets.QLineEdit(str(values[index]))
            field.setObjectName("offset_value_" + str(index))
            field.textChanged.connect(self.offset_changed)
            self.offset_labels.append(label)
            self.offset_fields.append(field)
            form.addRow(label, field)
        self.offset_mode.currentIndexChanged.connect(self.offset_changed)
        self.form.addWidget(group)
        self.offset_changed()

    def offset_changed(self):
        mode = self.offset_mode.currentData()
        labels = {"none": ("", "", ""), "rectangular": ("Delta XC", "Delta YC", "Delta ZC"),
                  "cylindrical": ("Radius", "Angle", "Delta ZC"),
                  "spherical": ("Radius", "Azimuth", "Elevation")}[mode]
        for label, field, text in zip(self.offset_labels, self.offset_fields, labels):
            label.setText(text)
            label.setVisible(mode != "none")
            field.setVisible(mode != "none")
        self.spec["offset"] = {"method": mode, "values": [field.text() for field in self.offset_fields]}
        self.changed()

    def option(self, name, value):
        self.spec[name] = value
        if name == "frame" and hasattr(self, "associative_wcs"):
            self.associative_wcs.setEnabled(value == "wcs")
        self.changed()

    def changed(self):
        if not self._building:
            self.owner.schedule_update()

    def method_changed(self):
        if self._building:
            return
        new = command_spec(self.spec["kind"], self.method.currentData())
        for key in ("frame", "associative", "associative_frame", "coordinate_mode"):
            if key in self.spec:
                new[key] = self.spec[key]
        mark_points(new, False)
        self.owner.replace_dialog(new)

    def curves_mode_changed(self):
        if self._building:
            return
        new = command_spec("plane", self.curves_mode.currentData())
        for key in ("frame", "associative", "associative_frame"):
            if key in self.spec:
                new[key] = self.spec[key]
        mark_points(new, False)
        self.owner.replace_dialog(new)

    def boolean_changed(self):
        self.spec["boolean"] = self.boolean.currentData()
        self.spec.setdefault("references", {}).setdefault("target", self.spec.get("target", []))
        self.collectors["target"].setVisible(self.spec["boolean"] != "none")
        self._boolean_visibility()
        self.activate_next()
        self.changed()

    def _boolean_visibility(self):
        visible = self.spec["kind"] in ("unite", "subtract", "intersect") or self.spec.get("boolean", "none") != "none"
        if hasattr(self, "keep_target"):
            self.keep_target.setVisible(visible)
            self.keep_tool.setVisible(visible)

    def clear_collector(self, name):
        definition = self.collectors[name].definition
        if definition.kind in ("point", "vector"):
            self.spec[name] = command_spec(definition.kind)
            self.spec[name]["_specified"] = False
        else:
            self.spec.setdefault("references", {})[name] = []
            if name == "target":
                self.spec["target"] = []
        self.activate_collector(name)
        self.changed()

    def activate_collector(self, name):
        self.active_collector = name
        self.refresh_collectors()
        self.owner.highlight()

    def collector_names(self):
        return [name for name, row in self.collectors.items()
                if not (name == "target" and self.spec["kind"] in ("block", "cylinder") and self.spec.get("boolean", "none") == "none")]

    def collector_complete(self, name):
        definition = self.collectors[name].definition
        if definition.kind in ("point", "vector"):
            return self.spec.get(name, {}).get("_specified", definition.kind == "vector")
        minimum = 2 if name == "objects" and self.spec["method"] not in ("curves_points",) else 1
        return len(self.spec.get("references", {}).get(name, [])) >= minimum

    def missing_collector(self):
        return next((name for name in self.collector_names() if not self.collector_complete(name)), None)

    def activate_next(self):
        names = self.collector_names()
        self.active_collector = self.missing_collector() or (names[-1] if names else None)
        self.refresh_collectors()

    def refresh_collectors(self):
        for name, row in self.collectors.items():
            row.refresh(self.spec, name == self.active_collector)

    def result(self, result):
        missing = self.missing_collector()
        valid = result.valid and missing is None
        self.ok.setEnabled(valid)
        self.apply.setEnabled(valid)
        self.show_result.setEnabled(valid)
        text = self.collectors[missing].definition.label if missing else result.diagnostic
        self.diagnostic.setText(text or "Preview ready")
        self.diagnostic.setStyleSheet("color: %s" % ("#49664c" if valid else "#a23d24"))
        self.refresh_collectors()

    def reject(self):
        self.owner.cancel_current()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and isinstance(self.focusWidget(), QtWidgets.QLineEdit):
            self.owner.update()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        if self in self.owner.dialogs:
            event.ignore()
            self.owner.cancel_current()
        else:
            event.accept()


class SelectionObserver:
    def __init__(self, owner):
        self.owner = owner

    def addSelection(self, doc, obj, sub, point):
        if not self.owner.selecting and self.owner.session.active:
            self.owner.collect(reference(doc, obj, sub, xyz(point)))


class Controller(QtCore.QObject):
    def __init__(self, adapter=None):
        super().__init__(Gui.getMainWindow())
        self._adapter_supplied = adapter is not None
        if adapter is None:
            if App.ActiveDocument is None:
                from .model import new_document
                new_document()
            from .model import ModelAdapter
            adapter = ModelAdapter()
        self.adapter = adapter
        self.session = CommandSession(adapter)
        self.dialogs = []
        self.preview = Preview()
        self.selecting = False
        self.before_selection = []
        self.completed = set()
        self.on_commit = None
        self.selection_manager = None
        self.selection_token = None
        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(90)
        self.timer.timeout.connect(self.update)
        self.observer = SelectionObserver(self)
        Gui.Selection.addObserver(self.observer)

    @property
    def dialog(self):
        return self.dialogs[-1] if self.dialogs else None

    def begin(self, kind, editing=None):
        if self.session.active:
            self.dialog.show()
            self.dialog.raise_()
            self.dialog.activateWindow()
            return self.dialog
        if App.ActiveDocument is None:
            from .model import new_document
            new_document()
        if not self._adapter_supplied:
            from .model import ModelAdapter
            self.adapter = ModelAdapter(App.ActiveDocument)
            self.session = CommandSession(self.adapter)
        self.before_selection = []
        for selection in Gui.Selection.getSelectionEx():
            subs = selection.SubElementNames or [""]
            self.before_selection.extend(reference(selection.DocumentName, selection.ObjectName, sub) for sub in subs)
        spec = self.adapter.load(editing) if editing is not None else command_spec(kind)
        mark_points(spec, editing is not None)
        if hasattr(self.adapter, "begin_context"):
            self.adapter.begin_context(spec, editing)
        self.session.begin(spec, editing=editing)
        if hasattr(Gui.Selection, "addSelectionGate"):
            from ..selection_filter import manager, Layer
            self.selection_manager = manager()
            layer = Layer("PRISMA Constructor", verdict=self.collector_verdict, alive=lambda: self.session.active)
            self.selection_token = self.selection_manager.push(layer)
        self._add_dialog(self.session.root)
        self.update()
        return self.dialog

    def _add_dialog(self, spec):
        if self.dialog:
            self.dialog.setEnabled(False)
        dialog = CommandDialog(self, spec, nested=bool(self.session.stack))
        self.dialogs.append(dialog)
        parent = Gui.getMainWindow()
        top_left = parent.mapToGlobal(QtCore.QPoint(max(230, parent.width()-dialog.sizeHint().width()-35), 165))
        if len(self.dialogs) > 1:
            top_left = self.dialogs[-2].pos() + QtCore.QPoint(28, 25)
        dialog.move(top_left)
        dialog.show()
        self.highlight()

    def replace_dialog(self, spec):
        self.timer.stop()
        old = self.dialogs.pop()
        position = old.pos()
        old.hide()
        old.deleteLater()
        self.session.update(spec)
        self._capture_view(spec)
        self._add_dialog(spec)
        self.dialog.move(position)
        self.update()

    def _capture_view(self, spec):
        if spec["method"] not in ("view_direction", "view_plane"):
            return
        try:
            view = Gui.activeDocument().activeView()
            spec["view_direction"] = xyz(view.getViewDirection())
            spec["view_rotation"] = list(view.getCameraOrientation().Q)
            camera = view.getCameraNode()
            eye = xyz(camera.position.getValue())
            distance = float(camera.focalDistance.getValue())
            spec["view_point"] = [eye[i] + distance * spec["view_direction"][i] for i in range(3)]
            spec["associative"] = False
        except Exception:
            pass

    def schedule_update(self):
        self.timer.start()

    def update(self):
        self.timer.stop()
        if not self.session.active or self.dialog is None:
            self.preview.clear()
            return
        result = self.session.update(self.dialog.spec)
        self.dialog.result(result)
        if self.dialog.missing_collector():
            self.preview.clear()
        else:
            try:
                self.preview.show(result, self.dialog.spec)
            except Exception as exc:
                self.dialog.diagnostic.setText("Preview unavailable: " + str(exc))
        return result

    def show_preview(self, visible):
        self.preview.visible = visible
        self.update()

    def show_result(self):
        result = self.update()
        if result is None or not result.valid or self.dialog.missing_collector():
            return False
        automatic = self.preview.visible
        try:
            self.preview.visible = True
            self.preview.show(result, self.dialog.spec)
            return True
        except Exception as exc:
            self.dialog.diagnostic.setText("Preview unavailable: " + str(exc))
            return False
        finally:
            self.preview.visible = automatic

    def open_constructor(self, name):
        self.update()
        parent = self.dialog
        definition = parent.collectors[name].definition
        initial = deepcopy(parent.spec.get(name) or command_spec(definition.kind))
        def accepted(spec):
            spec["_specified"] = True
            parent.spec[name] = spec
            parent.activate_next()
        self.session.push(definition.kind, initial, on_accept=accepted)
        self._add_dialog(self.session.current)
        self.update()

    def _remove_dialog(self):
        old = self.dialogs.pop()
        old.hide()
        old.deleteLater()
        if self.dialog:
            self.dialog.setEnabled(True)
            self.dialog.raise_()

    def accept(self):
        self.update()
        if not self.dialog or not self.dialog.ok.isEnabled():
            return False
        try:
            if self.session.stack:
                self.session.pop(self.dialog.spec)
                self._remove_dialog()
                # pop updates a copied root; the remaining widget owns the live recipe.
                self.session.update(self.dialog.spec)
                self.highlight()
                self.update()
            else:
                kind = self.dialog.spec["kind"]
                feature = self.session.accept()
                self._finished()
                self._committed(kind, feature)
            return True
        except Exception as exc:
            self.dialog.result(PreviewResult(False, error=str(exc)))
            return False

    def apply(self):
        self.update()
        if self.session.stack or not self.dialog or not self.dialog.apply.isEnabled():
            return False
        try:
            feature = self.session.apply()
            if self.session.editing is None and self.dialog.spec["kind"] in ("unite", "subtract", "intersect"):
                self.dialog.spec["references"]["target"] = []
                self.dialog.spec["references"]["tools"] = []
                self.dialog.activate_next()
                self.highlight()
            self._committed(self.dialog.spec["kind"], feature)
            self.update()
            return True
        except Exception as exc:
            self.dialog.result(PreviewResult(False, error=str(exc)))
            return False

    def _committed(self, kind, feature):
        self.completed.add(KIND_COMMANDS.get(kind, kind))
        if self.on_commit:
            self.on_commit(kind, feature)

    def cancel_current(self):
        self.timer.stop()
        if self.session.stack:
            self.session.pop(accepted=False)
            self._remove_dialog()
            self.session.update(self.dialog.spec)
            self.highlight()
            self.update()
        else:
            self.session.cancel()
            self._finished()

    def _finished(self):
        self.timer.stop()
        self.preview.clear()
        while self.dialogs:
            self._remove_dialog()
        if self.selection_manager is not None and self.selection_token is not None:
            self.selection_manager.pop(self.selection_token)
            self.selection_token = None
        self.selecting = True
        try:
            Gui.Selection.clearSelection()
            for ref in self.before_selection:
                try:
                    Gui.Selection.addSelection(ref["document"], ref["object"], ref["subelement"])
                except (RuntimeError, ValueError):
                    pass  # an old document/reference may have closed with the command
        finally:
            self.selecting = False

    def middle_click(self, modifiers=Qt.NoModifier):
        if not self.session.active:
            return False
        self.update()
        missing = self.dialog.missing_collector()
        if missing:
            self.dialog.activate_collector(missing)
            self.dialog.diagnostic.setText("Complete the highlighted collector before continuing")
        elif modifiers & Qt.ControlModifier and not self.session.stack:
            self.apply()
        else:
            self.accept()
        return True

    def collect(self, ref):
        dialog = self.dialog
        if dialog is None or not dialog.active_collector:
            return False
        name = dialog.active_collector
        definition = dialog.collectors[name].definition
        if self.selection_manager is not None:
            try:
                allowed, reason, picked = self.selection_manager.verdict(ref["document"], ref["object"], ref.get("subelement", ""))
                if not allowed:
                    dialog.diagnostic.setText(reason)
                    self.selection_manager.note_rejection(picked, reason)
                    return True
            except (RuntimeError, ValueError) as exc:
                dialog.diagnostic.setText(str(exc))
                return True
        if definition.kind == "point":
            spec = command_spec("point", "existing" if ref.get("subelement", "").startswith("Vertex") else "inferred")
            spec["references"]["object"] = [ref]
            spec["_specified"] = True
            dialog.spec[name] = spec
        elif definition.kind == "vector":
            spec = command_spec("vector", "inferred")
            spec["references"]["object"] = [ref]
            spec["_specified"] = True
            dialog.spec[name] = spec
        else:
            if definition.kind == "body":
                ref = deepcopy(ref)
                try:
                    obj = App.getDocument(ref["document"]).getObject(ref["object"])
                    if ref.get("subelement") and "." in ref["subelement"]:
                        from ..selection_filter import _leaf
                        obj, _element = _leaf(obj, ref["subelement"])
                        ref["object"] = obj.Name
                    ref["subelement"] = ""
                    if not getattr(obj, "Shape", None) or not obj.Shape.Solids:
                        dialog.diagnostic.setText("Select a solid body")
                        return True
                except Exception:
                    dialog.diagnostic.setText("Select a solid body in the active part")
                    return True
            refs = dialog.spec.setdefault("references", {}).setdefault(name, [])
            maximum = 2 if name == "objects" else 1
            if name == "objects" and dialog.spec["method"] == "curves_points":
                maximum = 64
            identity = reference_identity(ref)
            if any(reference_identity(item) == identity for item in refs):
                refs[:] = [item for item in refs if reference_identity(item) != identity]
            elif len(refs) >= maximum:
                refs[:] = [ref]
            else:
                refs.append(ref)
            if name == "target" and dialog.spec["kind"] in ("block", "cylinder"):
                dialog.spec["target"] = deepcopy(refs)
        dialog.activate_next()
        self.highlight()
        self.update()
        return True

    def collector_verdict(self, pick):
        if self.selecting or self.dialog is None or not self.dialog.active_collector:
            return True, ""
        from ..selection_filter import type_ok
        dialog = self.dialog
        definition = dialog.collectors[dialog.active_collector].definition
        if definition.kind == "body":
            ok = type_ok("body", pick.top_obj, pick.sub)
            return ok, "Select a solid body" if not ok else ""
        if definition.kind in ("point", "vector"):
            allowed = ("vertex", "edge", "face", "datum", "curve")
        else:
            method = dialog.spec["method"]
            allowed = {"existing": ("vertex", "datum"), "end_point": ("vertex", "edge", "datum"),
                       "midpoint": ("edge", "curve"), "on_curve": ("edge", "curve", "datum"),
                       "on_face": ("face",), "face_normal": ("face", "datum"),
                       "two_lines": ("edge", "curve", "datum"), "curves_points": ("edge", "curve", "vertex", "datum")}.get(method)
            if allowed is None:
                allowed = ("edge", "face", "vertex", "datum", "curve")
            if definition.name == "axis":
                allowed = ("edge", "datum")
        ok = pick.kind in allowed
        return ok, "Select geometry for " + definition.label.lower() if not ok else ""

    def highlight(self):
        if not self.dialog:
            return
        self.selecting = True
        try:
            Gui.Selection.clearSelection()
            refs = []
            spec = self.dialog.spec
            for values in spec.get("references", {}).values():
                refs.extend(values)
            for name in self.dialog.collectors:
                nested = spec.get(name)
                if isinstance(nested, dict):
                    for values in nested.get("references", {}).values():
                        refs.extend(values)
            for ref in refs:
                Gui.Selection.addSelection(ref.get("document", ""), ref["object"], ref.get("subelement", ""))
        finally:
            self.selecting = False

    def pointer(self, mouse, point):
        """Consume an MB1 pick in a constructor, including empty-space points."""
        info = mouse.pick(point)
        dialog = self.dialog
        point_methods = ("coordinates", "cursor", "point_expression")
        coordinate_point = dialog.spec["kind"] == "point" and dialog.spec["method"] in point_methods
        if info and info.get("Object") and not coordinate_point:
            doc = info.get("Document") or App.ActiveDocument.Name
            obj = info.get("ParentObject") or info["Object"]
            obj = getattr(obj, "Name", obj)
            sub = info.get("SubName") if info.get("ParentObject") else info.get("Component", "")
            return self.collect(reference(doc, obj, sub, (info.get("x", 0), info.get("y", 0), info.get("z", 0))))
        nested_point = dialog.active_collector and dialog.collectors[dialog.active_collector].definition.kind == "point"
        if nested_point or coordinate_point:
            x, y = mouse.coin_xy(point)
            frame = None
            if hasattr(self.adapter, "doc"):
                from .model import get_wcs
                frame = get_wcs(self.adapter.doc)
            try:
                value = (info["x"], info["y"], info["z"]) if info and "x" in info else workplane_point(mouse.view, x, y, frame)
            except ValueError as exc:
                dialog.diagnostic.setText(str(exc))
                return True
            reference_frame = dialog.spec.get("frame", "absolute")
            if reference_frame == "wcs" and frame is not None:
                value = frame.inverse().multVec(App.Vector(*xyz(value)))
            spec = command_spec("point", "coordinates")
            spec["frame"] = reference_frame
            spec["fields"].update(zip(("x", "y", "z"), (format(v, ".12g") for v in xyz(value))))
            spec["_specified"] = True
            if nested_point:
                dialog.spec[dialog.active_collector] = spec
                dialog.activate_next()
            else:
                if hasattr(dialog, "coordinate_mode"):
                    dialog.coordinate_mode.setCurrentIndex(dialog.coordinate_mode.findData("cartesian"))
                for name, value in spec["fields"].items():
                    dialog.editors[name].setText(value)
            self.update()
            return True
        return True  # a command's collector owns MB1, even if this pick is invalid

    def context_items(self):
        dialog = self.dialog
        return [
            {"label": "OK", "run": self.accept, "enabled": dialog.ok.isEnabled()},
            {"label": "Apply", "run": self.apply, "enabled": not self.session.stack and dialog.apply.isEnabled()},
            {"label": "Next Collector", "run": dialog.activate_next},
            {"label": "Clear Collector", "run": lambda: dialog.clear_collector(dialog.active_collector),
             "enabled": bool(dialog.active_collector)},
            {"label": "Cancel", "run": self.cancel_current}]

    def context_menu(self, global_point):
        self.update()
        menu = QtWidgets.QMenu(Gui.getMainWindow())
        for item in self.context_items():
            action = menu.addAction(item["label"])
            action.setEnabled(item.get("enabled", True))
            action.triggered.connect(item["run"])
        exec_menu(menu, global_point)
        menu.deleteLater()
        return True
