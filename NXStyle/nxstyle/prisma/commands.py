# SPDX-License-Identifier: LGPL-2.1-or-later
"""Only the native PRISMA command routes; no legacy feature dialogs are loaded."""

import FreeCAD as App
import FreeCADGui as Gui

from ..qt import QAction, QtCore, QtGui, QtWidgets, Qt
from .catalog import COMMANDS

_commands = {}
_wcs_dialog = None


def selected_feature():
    for obj in Gui.Selection.getSelection():
        if "PrismaDefinition" in getattr(obj, "PropertiesList", ()):
            return obj
    return None


class Command:
    def __init__(self, name):
        self.name = name
        self.kind, self.label, self.icon = COMMANDS[name]

    def GetResources(self):
        return {"MenuText": self.label, "ToolTip": self.label, "Pixmap": self.icon}

    def IsActive(self):
        from .ui import current
        if self.kind == "role":
            return current() is None
        if self.kind == "edit":
            return current() is None and selected_feature() is not None
        if self.kind == "wcs":
            return current() is None and App.ActiveDocument is not None
        return current() is None

    def Activated(self):
        from . import ui
        if self.kind == "edit":
            feature = selected_feature()
            if feature is not None:
                ui.launch(None, editing=feature)
        elif self.kind == "wcs":
            show_wcs()
        elif self.kind == "role":
            from .boot import instance
            shell = instance()
            if shell:
                shell.import_role()
        else:
            ui.launch(self.kind)


def register(parent=None):
    """Own host QActions avoid loading old workbenches just to create actions."""
    from ..ribbon import HOST_ACTIONS
    for name in COMMANDS:
        if name not in _commands:
            command = Command(name)
            _commands[name] = command
            Gui.addCommand(name, command)
        command = _commands[name]
        if name not in HOST_ACTIONS:
            action = QAction(QtGui.QIcon(command.icon), command.label, parent or Gui.getMainWindow())
            action.setObjectName(name)
            action.setToolTip(command.label)
            action.triggered.connect(command.Activated)
            HOST_ACTIONS[name] = action
    return dict(_commands)


def update_actions():
    from ..ribbon import HOST_ACTIONS
    for name, command in _commands.items():
        action = HOST_ACTIONS.get(name)
        if action is not None:
            action.setEnabled(command.IsActive())


class WCSDialog(QtWidgets.QDialog):
    """A work coordinate frame, independent of existing feature geometry."""
    def __init__(self):
        super().__init__(Gui.getMainWindow(), Qt.Tool)
        from .model import get_wcs
        self.doc = App.ActiveDocument
        frame = get_wcs(self.doc)
        self.setWindowTitle("WCS")
        self.setObjectName("PrismaWCSDialog")
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel("Origin in Absolute coordinates"))
        form = QtWidgets.QFormLayout()
        self.fields = {}
        values = tuple(frame.Base) + tuple(reversed(frame.Rotation.toEuler()))
        for label, value in zip(("X", "Y", "Z", "Rotation X", "Rotation Y", "Rotation Z"), values):
            field = QtWidgets.QLineEdit(format(value, ".12g"))
            field.setObjectName("wcs_" + label.lower().replace(" ", "_"))
            form.addRow(label, field)
            self.fields[label] = field
        layout.addLayout(form)
        reset = QtWidgets.QPushButton("Reset to Absolute")
        reset.clicked.connect(self.reset)
        layout.addWidget(reset)
        selection = QtWidgets.QPushButton("Orient to Selected Datum")
        selection.clicked.connect(self.from_selected)
        layout.addWidget(selection)
        self.diagnostic = QtWidgets.QLabel()
        self.diagnostic.setWordWrap(True)
        layout.addWidget(self.diagnostic)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def reset(self):
        for field in self.fields.values():
            field.setText("0")

    def from_selected(self):
        objects = Gui.Selection.getSelection()
        if not objects or not hasattr(objects[0], "Placement"):
            self.diagnostic.setText("Select a datum or coordinate system with a placement")
            return
        obj = objects[0]
        frame = obj.getGlobalPlacement() if hasattr(obj, "getGlobalPlacement") else obj.Placement
        for field, value in zip(self.fields.values(), tuple(frame.Base) + tuple(reversed(frame.Rotation.toEuler()))):
            field.setText(format(value, ".12g"))

    def accept(self):
        from .model import set_wcs
        try:
            values = [float(App.Units.Quantity(field.text()).Value) for field in self.fields.values()]
            rotation = App.Rotation(values[5], values[4], values[3])
            set_wcs(self.doc, App.Placement(App.Vector(*values[:3]), rotation))
            super().accept()
        except Exception as exc:
            self.diagnostic.setText(str(exc))


def show_wcs():
    global _wcs_dialog
    if _wcs_dialog is None or not _wcs_dialog.isVisible():
        _wcs_dialog = WCSDialog()
    _wcs_dialog.show()
    _wcs_dialog.raise_()
    return _wcs_dialog
