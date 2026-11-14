# SPDX-License-Identifier: LGPL-2.1-or-later
"""Opt-in PRISMA shell. Importing it does not start NXStyle or vendor add-ons."""

import json
import os
from pathlib import Path

import FreeCAD as App
import FreeCADGui as Gui

from ..qt import QAction, QtCore, QtGui, QtWidgets, Qt

_shell = None


def instance():
    return _shell


def schedule():
    if os.environ.get("PRISMA_PROFILE") == "1":
        QtCore.QTimer.singleShot(0, _start)


def _start():
    global _shell
    mw = Gui.getMainWindow()
    if mw is None or not mw.isVisible():
        QtCore.QTimer.singleShot(200, _start)
        return
    if _shell is None:
        _shell = Shell(mw)
        _shell.setup()


class Shell(QtCore.QObject):
    def __init__(self, mw):
        super().__init__(mw)
        self.mw = mw
        self.ribbon = self.toolbar = None
        self.selection_bar = None
        self.layout = None
        self.role_shortcuts = []
        self._tree_signature = None
        self.preferences = {}
        root = os.environ.get("PRISMA_USER_DATA")
        self.profile = Path(root) if root else Path(App.getUserConfigDir()) / "prisma"
        self.profile.mkdir(parents=True, exist_ok=True)
        self.layout_path = self.profile / "basic-modeling.json"
        self.preferences_path = self.profile / "ui.json"

    def setup(self):
        from .. import config, prefs, theme
        from ..navigation import NavigationManager
        from . import commands
        from .catalog import basic_layout
        config._cfg = dict(config.DEFAULTS, plm="none", nxt=False, lcinterlocking=False,
                           new_part_datum=False, auto_workbench=False, navigation="nx")
        prefs.apply(config._cfg)
        self.apply_theme()
        commands.register(self.mw)
        self.commands = commands
        self.layout = basic_layout()
        try:
            if self.layout_path.exists():
                saved = json.loads(self.layout_path.read_text(encoding="utf-8"))
                if isinstance(saved.get("tabs"), list):
                    self.layout = saved
            else:
                from .roles import save_layout
                save_layout(self.layout_path, self.layout)
            if self.preferences_path.exists():
                self.preferences = json.loads(self.preferences_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            App.Console.PrintWarning("PRISMA profile: %s\n" % exc)
        self.build_ribbon()
        self.build_selection_bar()
        self.build_navigator()
        self.navigation = NavigationManager(self.mw)
        self.navigation.scan()
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()
        from .catalog import COMMANDS
        from .roles import should_offer_role_import, dismiss_role_import
        if should_offer_role_import(self.preferences, COMMANDS):
            dismiss_role_import(self.preferences)
            self.save_preferences()
            QtCore.QTimer.singleShot(1500, self.offer_role)
        self.mw.setWindowTitle("PRISMA — Basic Modeling")
        self.mw.statusBar().showMessage("PRISMA Basic Modeling · MB2 drag Rotate · Shift+MB2 Pan · Ctrl+MB2 Zoom · Hold MB3 Radial", 12000)

    def build_ribbon(self):
        from ..ribbon import Ribbon, HOST_ACTIONS, spec_groups, known_commands
        from ..finder import CommandFinder
        if self.toolbar is not None:
            self.mw.removeToolBar(self.toolbar)
            self.toolbar.deleteLater()
        self.ribbon = Ribbon(self.mw)
        self.ribbon.app_id = "prisma"
        known = known_commands() | set(HOST_ACTIONS)
        for tab in self.layout["tabs"]:
            groups = []
            for name, items, meta in spec_groups(tab):
                converted = []
                for item in items:
                    if item.get("cmd") in known and item.get("enabled", True):
                        converted.append(item)
                    else:
                        placeholder = QtWidgets.QToolButton()
                        placeholder.setText(item.get("label", item.get("external_id", "Unavailable")))
                        placeholder.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
                        placeholder.setEnabled(False)
                        placeholder.setToolTip(item.get("diagnostic", "Command unavailable in PRISMA"))
                        placeholder.setProperty("nxsize", "large")
                        converted.append(placeholder)
                groups.append((name, converted, meta))
            self.ribbon.add_tab(tab.get("name", "Modeling"), groups, known=known)
        finder = CommandFinder(self.ribbon)
        self.ribbon.corner.addWidget(finder)
        file_menu = QtWidgets.QMenu(self.ribbon.file_button)
        for label, command in (("New", "Std_New"), ("Open…", "Std_Open"), ("Save", "Std_Save"),
                               ("Save As…", "Std_SaveAs"), ("Import…", "Std_Import"), ("Export…", "Std_Export"),
                               ("Undo", "Std_Undo"), ("Redo", "Std_Redo")):
            action = file_menu.addAction(label)
            action.triggered.connect(lambda _=False, n=command: Gui.runCommand(n))
        file_menu.addSeparator()
        file_menu.addAction("Import NX Role…", self.import_role)
        self.ribbon.file_button.setMenu(file_menu)
        self.toolbar = QtWidgets.QToolBar("PRISMA Ribbon", self.mw)
        self.toolbar.setObjectName("PrismaRibbonToolbar")
        self.toolbar.setMovable(False)
        self.toolbar.setFloatable(False)
        self.toolbar.addWidget(self.ribbon)
        self.mw.addToolBar(Qt.TopToolBarArea, self.toolbar)
        if self.selection_bar is not None:
            self.mw.insertToolBar(self.selection_bar, self.toolbar)
            self.mw.insertToolBarBreak(self.selection_bar)
        self.install_role_shortcuts()

    def apply_theme(self):
        from .. import prefs, config, theme
        app = QtWidgets.QApplication.instance()
        directories = [Path(App.getUserAppDataDir()) / "Gui" / "Stylesheets",
                       Path(App.getResourceDir()) / "Gui" / "Stylesheets"]
        existing = list(QtCore.QDir.searchPaths("qss"))
        QtCore.QDir.setSearchPaths("qss", [str(path) for path in directories if path.is_dir()] + existing)
        base = app.styleSheet().split("/* NXStyle theme */")[0]
        if len(base) < 1000:
            for directory in directories:
                path = directory / prefs.base_stylesheet(config._cfg)
                if path.is_file():
                    base = path.read_text(encoding="utf-8")
                    break
        app.setStyleSheet(theme.fill_freecad(base) + "\n" + theme.stylesheet("nx_theme.qss"))

    def install_role_shortcuts(self):
        for shortcut in self.role_shortcuts:
            shortcut.setEnabled(False)
            shortcut.deleteLater()
        self.role_shortcuts = []
        shortcut_type = getattr(QtGui, "QShortcut", None) or QtWidgets.QShortcut
        for item in self.layout.get("shortcuts", []):
            name = item.get("cmd")
            if not item.get("enabled", True) or name not in self.commands._commands or not item.get("key"):
                continue
            shortcut = shortcut_type(QtGui.QKeySequence(item["key"]), self.mw)
            shortcut.setObjectName("PrismaRoleShortcut_" + name)
            shortcut.activated.connect(lambda n=name: self.run_role_command(n))
            self.role_shortcuts.append(shortcut)

    def run_role_command(self, name):
        command = self.commands._commands.get(name)
        if command is not None and command.IsActive():
            command.Activated()

    def role_radial_items(self, info=None):
        from .catalog import COMMANDS
        radials = self.layout.get("radials", [])
        if not radials:
            return []
        context = "object" if info and info.get("Object") else "view"
        radial = next((r for r in radials if r.get("context") == context), radials[0])
        items = []
        for item in radial.get("items", []):
            name = item.get("cmd")
            command = self.commands._commands.get(name)
            icon = QtGui.QIcon(COMMANDS[name][2]) if name in COMMANDS else QtGui.QIcon()
            items.append({"label": item.get("label", name or "Unavailable"), "icon": icon,
                          "enabled": bool(item.get("enabled", True) and command is not None and command.IsActive()),
                          "run": lambda n=name: self.run_role_command(n)})
        return items

    def build_navigator(self):
        self.navigator = QtWidgets.QDockWidget("Part Navigator", self.mw)
        self.navigator.setObjectName("PrismaPartNavigator")
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setObjectName("PrismaFeatureTree")
        self.tree.setStyleSheet("QTreeWidget { background: white; color: #243447; border: none; }"
                                "QTreeWidget::item { padding: 4px 2px; }"
                                "QTreeWidget::item:selected { background: #d5ecef; color: #243447; }"
                                "QHeaderView::section { background: #f0f4f8; color: #526579; padding: 5px; border: none; }")
        self.tree.setHeaderLabels(["Name", "Type"])
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.tree.setColumnWidth(0, 170)
        self.tree.itemSelectionChanged.connect(self.select_tree)
        self.tree.itemDoubleClicked.connect(self.edit_tree)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.tree_menu)
        self.navigator.setWidget(self.tree)
        self.mw.addDockWidget(Qt.LeftDockWidgetArea, self.navigator)

    def build_selection_bar(self):
        from ..selection_filter import TypeFilter, ScopeFilter, CurveRule, manager
        self.selection_manager = manager()
        self.selection_bar = QtWidgets.QToolBar("Selection", self.mw)
        self.selection_bar.setObjectName("NXTopBorderBar")
        self.selection_bar.setMovable(False)
        self.selection_bar.setFloatable(False)
        self.type_filter = TypeFilter(self.selection_bar)
        self.scope_filter = ScopeFilter(self.selection_bar)
        self.curve_rule = CurveRule(self.selection_bar)
        self.curve_rule.setEnabled(False)
        self.curve_rule.setToolTip("Curve chaining is available for section selection; these constructors select one reference at a time")
        for label, widget in (("Type", self.type_filter), ("Scope", self.scope_filter), ("Curve Rule", self.curve_rule)):
            self.selection_bar.addWidget(QtWidgets.QLabel(label))
            self.selection_bar.addWidget(widget)
            self.selection_bar.addSeparator()
        clear = QtWidgets.QPushButton("Deselect All")
        clear.clicked.connect(lambda: Gui.Selection.clearSelection())
        self.selection_bar.addWidget(clear)
        self.selection_cue = QtWidgets.QLabel()
        self.selection_bar.addWidget(self.selection_cue)
        self.mw.addToolBarBreak(Qt.TopToolBarArea)
        self.mw.addToolBar(Qt.TopToolBarArea, self.selection_bar)

    def select_tree(self):
        from .ui import current, reference
        active = current()
        if active is None:
            Gui.Selection.clearSelection()
        for item in self.tree.selectedItems():
            identity = item.data(0, Qt.UserRole)
            if not identity:
                continue
            doc, name = identity
            if active is not None:
                active.collect(reference(doc, name))
            else:
                allowed, reason, picked = self.selection_manager.verdict(doc, name, "")
                if allowed:
                    Gui.Selection.addSelection(doc, name)
                else:
                    self.selection_manager.note_rejection(picked, reason)

    def edit_tree(self, item, _column):
        identity = item.data(0, Qt.UserRole)
        if not identity:
            return
        obj = App.getDocument(identity[0]).getObject(identity[1])
        if "PrismaDefinition" in getattr(obj, "PropertiesList", ()):
            from .ui import launch
            launch(None, editing=obj)

    def tree_menu(self, point):
        from ..qt import exec_menu
        menu = QtWidgets.QMenu(self.tree)
        edit = menu.addAction("Edit Parameters")
        edit.setEnabled(self.commands.selected_feature() is not None)
        edit.triggered.connect(lambda: Gui.runCommand("Prisma_EditParameters"))
        menu.addAction("Show / Hide", lambda: Gui.runCommand("Std_ToggleVisibility"))
        exec_menu(menu, self.tree.viewport().mapToGlobal(point))

    def refresh(self):
        from .ui import current
        if "/* NXStyle theme */" not in QtWidgets.QApplication.instance().styleSheet():
            self.apply_theme()
        self.commands.update_actions()
        for toolbar in self.mw.findChildren(QtWidgets.QToolBar):
            if toolbar is not self.toolbar and toolbar is not self.selection_bar:
                toolbar.hide()
        for dock in self.mw.findChildren(QtWidgets.QDockWidget):
            if dock.objectName() in ("Combo View", "ComboView", "Model", "Tasks"):
                dock.hide()
        self.selection_manager.sync()
        self.selection_cue.setText(self.selection_manager.recent_rejection())
        doc = App.ActiveDocument
        if doc is not None:
            part = next((obj for obj in doc.Objects if getattr(obj, "PrismaModel", False)), None)
            if part is not None:
                try:
                    view = Gui.activeDocument().activeView()
                    if view.getActiveObject("part") is None:
                        view.setActiveObject("part", part)
                except (RuntimeError, AttributeError):
                    pass
        origins = set()
        if doc:
            for obj in doc.Objects:
                if obj.TypeId == "App::Origin":
                    origins.add(obj.Name)
                    origins.update(feature.Name for feature in getattr(obj, "OriginFeatures", ()))
        objects = [] if doc is None else [o for o in doc.Objects if not getattr(o, "PrismaInternal", False) and o.Name not in origins]
        title = "PRISMA — " + (doc.Label if doc else "Basic Modeling")
        if self.mw.windowTitle() != title:
            self.mw.setWindowTitle(title)
        signature = (doc.Name if doc else None, tuple((o.Name, o.Label, getattr(o.ViewObject, "Visibility", True),
                                                     str(getattr(o, "State", ()))) for o in objects))
        if signature != self._tree_signature:
            self._tree_signature = signature
            self.tree.blockSignals(True)
            self.tree.clear()
            if doc:
                root = QtWidgets.QTreeWidgetItem([doc.Label, "Work Part"])
                self.tree.addTopLevelItem(root)
                for obj in objects:
                    if getattr(obj, "PrismaModel", False):
                        continue
                    label = str(getattr(obj, "TypeId", "")).replace("Prisma::", "")
                    item = QtWidgets.QTreeWidgetItem([obj.Label, label])
                    item.setData(0, Qt.UserRole, (doc.Name, obj.Name))
                    if not getattr(obj.ViewObject, "Visibility", True):
                        item.setForeground(0, QtGui.QColor("#87929a"))
                    root.addChild(item)
                root.setExpanded(True)
            self.tree.blockSignals(False)
        active = current()
        if active:
            active.on_commit = self.committed
            try:
                if active.adapter.doc is not App.ActiveDocument:
                    while active.session.active:
                        active.cancel_current()
            except AttributeError:
                pass

    def committed(self, _kind, _feature):
        from .catalog import COMMANDS
        from .roles import should_offer_role_import, dismiss_role_import
        if should_offer_role_import(self.preferences, COMMANDS):
            dismiss_role_import(self.preferences)
            self.save_preferences()
            QtCore.QTimer.singleShot(0, self.offer_role)

    def offer_role(self):
        from .ui import current
        if current() is not None:
            QtCore.QTimer.singleShot(2000, self.offer_role)
            return
        # Nonmodal and dismissible; the default role remains a complete preset.
        self.role_offer = QtWidgets.QMessageBox(self.mw)
        self.role_offer.setWindowTitle("Optional NX Role")
        self.role_offer.setText("Basic Modeling is ready. You can keep this layout or import an NX role layout.")
        self.role_offer.addButton("Keep Basic Modeling", QtWidgets.QMessageBox.RejectRole)
        import_button = self.role_offer.addButton("Import Role…", QtWidgets.QMessageBox.AcceptRole)
        import_button.clicked.connect(self.import_role)
        self.role_offer.setModal(False)
        self.role_offer.show()

    def import_role(self):
        from .roles import read_role, save_layout, required_role_references, selected_role_sources, MAX_ROLE_BYTES
        path, _filter = QtWidgets.QFileDialog.getOpenFileName(self.mw, "Import NX Role", "", "NX Role (*.mtx *.xml);;All Files (*)")
        if not path:
            return
        try:
            role_path = Path(path)
            if role_path.stat().st_size > MAX_ROLE_BYTES:
                raise ValueError("The role exceeds the 4 MB import limit")
            content = role_path.read_bytes()
            inherited = {}
            skipped = set()
            for _depth in range(16):
                missing = [name for name in required_role_references(content, inherited) if name not in skipped]
                if not missing:
                    break
                for name in missing:
                    chosen, _filter = QtWidgets.QFileDialog.getOpenFileName(self.mw,
                        "Select inherited NX role: " + name + " (Cancel uses Basic Modeling)", str(role_path.parent),
                        "NX Role (*.mtx *.xml);;All Files (*)")
                    if chosen:
                        selected = Path(chosen)
                        sources = selected_role_sources([selected], [selected.parent])
                        inherited.update(sources)
                        inherited[name] = sources[selected.name]
                    else:
                        skipped.add(name)
            result = read_role(path, inherited=inherited)
        except (OSError, ValueError) as exc:
            QtWidgets.QMessageBox.warning(self.mw, "Role Import", str(exc))
            return
        dialog = QtWidgets.QDialog(self.mw)
        dialog.setWindowTitle("Review NX Role Layout")
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(QtWidgets.QLabel(result.layout.get("name", "Imported Role")))
        report = QtWidgets.QPlainTextEdit()
        report.setReadOnly(True)
        report.setPlainText("\n".join(result.diagnostics + ["Unavailable: " + c for c in result.unknown_commands]))
        layout.addWidget(report)
        preview = QtWidgets.QTreeWidget()
        preview.setHeaderLabels(["Imported layout", "Command"])
        for tab in result.layout["tabs"]:
            tab_item = QtWidgets.QTreeWidgetItem([tab.get("name", "")])
            preview.addTopLevelItem(tab_item)
            for group in tab["groups"]:
                group_item = QtWidgets.QTreeWidgetItem([group["name"]])
                tab_item.addChild(group_item)
                for item in group["items"]:
                    child = QtWidgets.QTreeWidgetItem([item.get("label", ""), item.get("cmd") or "Unavailable"])
                    group_item.addChild(child)
        for label, key in (("Keyboard Shortcuts", "shortcuts"), ("Radial Menus", "radials")):
            root = QtWidgets.QTreeWidgetItem([label])
            preview.addTopLevelItem(root)
            for item in result.layout.get(key, []):
                if key == "shortcuts":
                    status = item.get("cmd") if item.get("enabled", True) else "Disabled: " + item.get("diagnostic", "unavailable")
                    root.addChild(QtWidgets.QTreeWidgetItem([item.get("key", ""), status or "Unavailable"]))
                else:
                    radial = QtWidgets.QTreeWidgetItem([item.get("name", "Imported Radial")])
                    root.addChild(radial)
                    for entry in item.get("items", []):
                        radial.addChild(QtWidgets.QTreeWidgetItem([entry.get("label", ""), entry.get("cmd") or "Unavailable"]))
        preview.expandAll()
        layout.addWidget(preview)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("Use Layout")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(600, 560)
        from ..qt import exec_dialog
        if exec_dialog(dialog) == QtWidgets.QDialog.Accepted:
            self.layout = result.layout
            save_layout(self.layout_path, self.layout)
            self.preferences["role_imported"] = True
            self.save_preferences()
            self.build_ribbon()

    def save_preferences(self):
        self.preferences_path.write_text(json.dumps(self.preferences, indent=2) + "\n", encoding="utf-8")
