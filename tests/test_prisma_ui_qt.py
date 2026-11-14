# SPDX-License-Identifier: LGPL-2.1-or-later
"""Real Qt interactions for root/nested command state; no CAD host required."""

from copy import deepcopy
import importlib
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6 import QtCore, QtGui, QtWidgets, QtTest
except ImportError:
    if os.environ.get("NXSTYLE_REQUIRE_QT") == "1":
        raise
    QtCore = None

PACKAGE = Path(__file__).resolve().parents[1] / "NXStyle" / "nxstyle"


@unittest.skipIf(QtCore is None, "PySide6 required for PRISMA constructor checks")
class PrismaUiQt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.package_name = "_prisma_ui_qt"
        package = types.ModuleType(cls.package_name)
        package.__path__ = [str(PACKAGE)]
        wrapper = types.ModuleType("PySide")
        wrapper.QtCore, wrapper.QtGui, wrapper.QtWidgets = QtCore, QtGui, QtWidgets
        cls.gui = types.ModuleType("FreeCADGui")
        cls.fc = types.ModuleType("FreeCAD")
        cls.host = patch.dict(sys.modules, {cls.package_name: package, "PySide": wrapper,
                                          "FreeCAD": cls.fc, "FreeCADGui": cls.gui})
        cls.host.start()
        cls.ui = importlib.import_module(cls.package_name + ".prisma.ui")
        cls.definitions = importlib.import_module(cls.package_name + ".prisma.definitions")

    @classmethod
    def tearDownClass(cls):
        cls.host.stop()
        for name in list(sys.modules):
            if name.startswith(cls.package_name + "."):
                del sys.modules[name]

    def setUp(self):
        self.mw = QtWidgets.QMainWindow()
        self.mw.resize(1100, 800)
        self.mw.show()
        self.gui.getMainWindow = lambda: self.mw
        self.selection = []
        self.gui.Selection = types.SimpleNamespace(addObserver=lambda observer: None,
            getSelectionEx=lambda: [], clearSelection=self.selection.clear,
            addSelection=lambda *args: self.selection.append(args))
        solid = types.SimpleNamespace(Shape=types.SimpleNamespace(Solids=[object()]))
        self.doc = types.SimpleNamespace(Name="Model", getObject=lambda name: solid)
        self.fc.ActiveDocument = self.doc
        self.fc.getDocument = lambda name: self.doc
        definitions = self.definitions
        test = self
        class Adapter:
            def __init__(self):
                self.features = []
            def preview(self, spec):
                values = spec.get("fields", {})
                invalid = any(str(v).strip() == "bad" for v in values.values())
                return definitions.PreviewResult(not invalid, error="Invalid expression" if invalid else "")
            def commit(self, spec, editing=None):
                result = types.SimpleNamespace(spec=deepcopy(spec)) if editing is None else editing
                result.spec = deepcopy(spec)
                self.features.append(result)
                return result
            def load(self, feature):
                return deepcopy(feature.spec)
        self.adapter = Adapter()
        self.command = self.ui.Controller(self.adapter)
        self.ui._controller = self.command
        self.command.preview = types.SimpleNamespace(clear=lambda: None, show=lambda *_: None, visible=True)
        self.app.processEvents()

    def tearDown(self):
        while self.command.session.active:
            self.command.cancel_current()
        self.command.timer.stop()
        self.ui._controller = None
        self.command.deleteLater()
        self.mw.close()
        self.mw.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)

    def click(self, widget):
        QtTest.QTest.mouseClick(widget, QtCore.Qt.LeftButton)
        self.app.processEvents()

    def test_unset_block_point_is_required_and_child_ok_does_not_commit(self):
        root = self.command.begin("block")
        self.assertFalse(root.ok.isEnabled())
        self.assertEqual(root.collectors["origin"].count.text(), "(0)")
        self.assertEqual(root.active_collector, "origin")
        self.command.open_constructor("origin")
        child = self.command.dialog
        self.assertIsNot(child, root)
        self.assertFalse(root.isEnabled())
        self.assertEqual(child.windowModality(), QtCore.Qt.NonModal)
        self.assertTrue(child.ok.isEnabled())
        self.click(child.ok)
        self.assertIs(self.command.dialog, root)
        self.assertTrue(root.isEnabled())
        self.assertTrue(root.ok.isEnabled())
        self.assertEqual(root.collectors["origin"].count.text(), "(1)")
        self.assertEqual(self.adapter.features, [])

    def test_nested_cancel_keeps_original_value_and_root_cancel_preserves_apply(self):
        root = self.command.begin("block")
        self.command.open_constructor("origin")
        self.command.dialog.editors["x"].setText("20 + 5")
        self.command.accept()
        self.command.open_constructor("origin")
        self.command.dialog.editors["x"].setText("100")
        self.command.cancel_current()
        self.assertEqual(root.spec["origin"]["fields"]["x"], "20 + 5")
        self.assertTrue(self.command.apply())
        self.assertEqual(len(self.adapter.features), 1)
        self.assertTrue(self.command.session.active)
        self.command.cancel_current()
        self.assertEqual(len(self.adapter.features), 1)
        self.assertEqual(self.adapter.features[0].spec["origin"]["fields"]["x"], "20 + 5")

    def test_return_in_expression_does_not_commit_parent(self):
        root = self.command.begin("point")
        root.editors["x"].setFocus()
        QtTest.QTest.keyClicks(root.editors["x"], "42")
        QtTest.QTest.keyClick(root.editors["x"], QtCore.Qt.Key_Return)
        self.app.processEvents()
        self.assertTrue(self.command.session.active)
        self.assertEqual(self.adapter.features, [])

    def test_all_declared_methods_build_matching_controls(self):
        for kind, methods in self.definitions.METHODS.items():
            with self.subTest(kind=kind):
                self.command.begin(kind)
                try:
                    self.assertEqual(self.command.dialog.method.count(), len(methods) - int(kind == "plane"))
                    for method in methods:
                        if kind == "plane" and method == "three_points":
                            index = self.command.dialog.method.findData("curves_points")
                            self.command.dialog.method.setCurrentIndex(index)
                            self.command.dialog.curves_mode.setCurrentIndex(self.command.dialog.curves_mode.findData("three_points"))
                            dialog = self.command.dialog
                            self.assertEqual(dialog.spec["method"], method)
                            continue
                        index = self.command.dialog.method.findData(method)
                        self.command.dialog.method.setCurrentIndex(index)
                        dialog = self.command.dialog
                        self.assertEqual(dialog.spec["method"], method)
                        fields = self.definitions.FIELD_SCHEMAS[kind][method]
                        for field in fields:
                            self.assertIn(field.name, dialog.collectors if field.kind in ("point", "vector", "reference", "body") else dialog.editors)
                        self.assertNotIn("NXStyle_", dialog.windowTitle())
                finally:
                    while self.command.session.active:
                        self.command.cancel_current()

    def test_mb2_routes_to_required_collector_then_child_then_root(self):
        root = self.command.begin("cylinder")
        self.assertTrue(self.command.middle_click())
        self.assertEqual(self.adapter.features, [])
        self.assertEqual(root.active_collector, "origin")
        self.command.open_constructor("origin")
        self.assertTrue(self.command.middle_click())
        self.assertIs(self.command.dialog, root)
        self.assertEqual(self.adapter.features, [])
        self.command.middle_click(QtCore.Qt.ControlModifier)
        self.assertEqual(len(self.adapter.features), 1)
        self.assertTrue(self.command.session.active)
        self.command.middle_click()
        self.assertEqual(len(self.adapter.features), 2)
        self.assertFalse(self.command.session.active)

    def test_body_collectors_are_binary_and_keep_settings_survive_apply(self):
        root = self.command.begin("subtract")
        self.command.collect(self.ui.reference("Model", "Block", "Face1"))
        self.assertEqual(root.active_collector, "tools")
        self.command.collect(self.ui.reference("Model", "Cylinder", "Face2"))
        self.assertEqual(root.spec["references"]["target"][0]["subelement"], "")
        self.assertEqual(len(root.spec["references"]["tools"]), 1)
        self.assertTrue(root.ok.isEnabled())
        root.keep_tool.setChecked(True)
        root.keep_target.setChecked(True)
        self.command.apply()
        recipe = self.adapter.features[0].spec
        self.assertTrue(recipe["keep_target"])
        self.assertTrue(recipe["keep_tool"])
        self.assertEqual(root.spec["references"]["target"], [])
        self.assertEqual(root.spec["references"]["tools"], [])
        self.assertEqual(root.active_collector, "target")
        self.assertFalse(root.ok.isEnabled())
        self.assertTrue(root.keep_target.isChecked())
        self.assertTrue(root.keep_tool.isChecked())

    def test_edit_reopens_original_method_and_raw_expressions(self):
        recipe = self.definitions.command_spec("block", "two_points_height")
        recipe["height"] = "unused"
        recipe["fields"]["height"] = "Model.Length / 2"
        feature = types.SimpleNamespace(spec=recipe)
        dialog = self.command.begin(None, editing=feature)
        self.assertEqual(dialog.method.currentData(), "two_points_height")
        self.assertEqual(dialog.editors["height"].text(), "Model.Length / 2")
        self.assertEqual(dialog.collectors["origin"].count.text(), "(1)")
        self.assertTrue(dialog.ok.isEnabled())

    def test_coordinate_modes_have_unambiguous_spherical_elevation_labels(self):
        dialog = self.command.begin("point")
        for mode, labels in (("cartesian", ("XC", "YC", "ZC")), ("cylindrical", ("Radius", "Angle", "ZC")),
                             ("spherical", ("Radius", "Azimuth", "Elevation"))):
            dialog.coordinate_mode.setCurrentIndex(dialog.coordinate_mode.findData(mode))
            self.assertEqual(dialog.spec["coordinate_mode"], mode)
            for name, label in zip(("x", "y", "z"), labels):
                self.assertEqual(dialog.findChild(QtWidgets.QLabel, "label_" + name).text(), label)

    def test_point_offsets_keep_expression_strings_in_the_recipe(self):
        dialog = self.command.begin("point")
        dialog.offset_mode.setCurrentIndex(dialog.offset_mode.findData("spherical"))
        for field, value in zip(dialog.offset_fields, ("Model.Radius + 2", "30 deg", "45 deg")):
            field.setText(value)
        self.assertEqual(dialog.spec["offset"], {"method": "spherical", "values": ["Model.Radius + 2", "30 deg", "45 deg"]})
        self.assertEqual([label.text() for label in dialog.offset_labels], ["Radius", "Azimuth", "Elevation"])
        self.command.apply()
        self.assertEqual(self.adapter.features[0].spec["offset"], dialog.spec["offset"])

    def test_invalid_expression_disables_all_completion_actions(self):
        dialog = self.command.begin("point")
        dialog.editors["x"].setText("bad")
        self.command.update()
        self.assertFalse(dialog.ok.isEnabled())
        self.assertFalse(dialog.apply.isEnabled())
        self.assertFalse(self.command.accept())
        self.assertEqual(self.adapter.features, [])

    def test_show_result_renders_manually_with_preview_off_without_committing(self):
        dialog = self.command.begin("point")
        shown = []
        def show(result, spec):
            if self.command.preview.visible:
                shown.append(deepcopy(spec))
        self.command.preview.show = show
        dialog.preview.setChecked(False)
        self.assertTrue(dialog.show_result.isEnabled())
        self.click(dialog.show_result)
        self.assertEqual(len(shown), 1)
        self.assertFalse(dialog.preview.isChecked())
        self.assertFalse(self.command.preview.visible)
        self.assertEqual(self.adapter.features, [])
        dialog.editors["x"].setText("bad")
        self.command.update()
        self.assertFalse(dialog.show_result.isEnabled())
        self.assertFalse(self.command.show_result())
        self.assertEqual(len(shown), 1)

    def navigation(self):
        # Use the real viewport event filter. Camera rendering is a host service.
        stub = types.ModuleType(self.package_name + ".commands")
        with patch.dict(sys.modules, {stub.__name__: stub}):
            module = importlib.import_module(self.package_name + ".navigation")
        module.config._cfg = dict(module.config.DEFAULTS)
        widget = QtWidgets.QWidget(self.mw)
        widget.resize(500, 400)
        widget.show()
        view = types.SimpleNamespace(getObjectInfo=lambda point: None, getPoint=lambda x, y: (12, 8, 3))
        mouse = module.NxMouse(view, widget)
        mouse.owns_mouse = lambda: True
        mouse.read_orbit = lambda: None
        mouse.rotate = lambda *args: None
        mouse.pan = lambda *args: None
        mouse.zoom = lambda *args: None
        widget.installEventFilter(mouse)
        return module, mouse, widget

    def test_raw_three_button_events_pick_apply_and_radial(self):
        self.command.begin("block")
        module, mouse, widget = self.navigation()
        self.fc.Console = types.SimpleNamespace(PrintWarning=lambda text: self.fail(text))
        with patch.dict(os.environ, {"PRISMA_PROFILE": "1"}):
            QtTest.QTest.mouseClick(widget, QtCore.Qt.MiddleButton, pos=QtCore.QPoint(60, 60))
            self.assertEqual(self.adapter.features, [])
            QtTest.QTest.mouseClick(widget, QtCore.Qt.LeftButton, pos=QtCore.QPoint(60, 60))
            self.assertEqual(self.command.dialog.spec["origin"]["fields"], {"x": "12", "y": "8", "z": "0"})
            QtTest.QTest.mouseClick(widget, QtCore.Qt.MiddleButton, QtCore.Qt.ControlModifier,
                                   pos=QtCore.QPoint(60, 60))
            self.assertEqual(len(self.adapter.features), 1)
            self.assertTrue(self.command.session.active)
            QtTest.QTest.mousePress(widget, QtCore.Qt.RightButton, pos=QtCore.QPoint(60, 60))
            mouse.radial_timer.stop()
            mouse._open_radial()
            self.assertIsNotNone(mouse.last_radial)
            self.assertEqual([item["label"] for item in mouse.last_radial.items],
                             ["OK", "Apply", "Next Collector", "Clear Collector", "Cancel"])
            mouse.last_radial.close()
        widget.removeEventFilter(mouse)
        mouse.deleteLater()
        widget.deleteLater()

    def test_cursor_intersection_uses_workplane_in_orthographic_and_perspective_views(self):
        view = types.SimpleNamespace(getPoint=lambda x, y: (2., 4., 5.), getViewDirection=lambda: (0., 0., -1.))
        self.assertEqual(self.ui.workplane_point(view, 1, 2), (2., 4., 0.))
        camera = types.SimpleNamespace(getTypeId=lambda: types.SimpleNamespace(getName=lambda: "PerspectiveCamera"),
                                       position=types.SimpleNamespace(getValue=lambda: (0., 0., 10.)))
        view.getCameraNode = lambda: camera
        self.assertEqual(self.ui.workplane_point(view, 1, 2), (4., 8., 0.))
        del view.getCameraNode
        view.getViewDirection = lambda: (1., 0., 0.)
        with self.assertRaisesRegex(ValueError, "parallel"):
            self.ui.workplane_point(view, 1, 2)

    def test_raw_cursor_pick_is_converted_into_translated_wcs_coordinates(self):
        self.command.begin("block")
        self.command.dialog.frame.setCurrentIndex(self.command.dialog.frame.findData("wcs"))
        module, mouse, widget = self.navigation()
        frame = types.SimpleNamespace(Base=(5., 0., 10.), Rotation=types.SimpleNamespace(multVec=lambda v: (0., 0., 1.)),
            inverse=lambda: types.SimpleNamespace(multVec=lambda v: tuple(v[i] - (5., 0., 10.)[i] for i in range(3))))
        self.fc.Vector = lambda *values: values
        self.command.adapter.doc = self.doc
        model = types.ModuleType(self.package_name + ".prisma.model")
        model.get_wcs = lambda doc: frame
        with patch.dict(sys.modules, {model.__name__: model}), patch.dict(os.environ, {"PRISMA_PROFILE": "1"}):
            QtTest.QTest.mouseClick(widget, QtCore.Qt.LeftButton, pos=QtCore.QPoint(60, 60))
        origin = self.command.dialog.spec["origin"]
        self.assertEqual(origin["frame"], "wcs")
        self.assertEqual(origin["fields"], {"x": "7", "y": "8", "z": "0"})
        widget.removeEventFilter(mouse)
        mouse.deleteLater()
        widget.deleteLater()

    def test_prisma_branding_reaches_file_button_and_new_document_windows(self):
        boot = importlib.import_module(self.package_name + ".prisma.boot")
        original_icon = self.app.windowIcon()
        original_name = self.app.applicationDisplayName()
        self.addCleanup(self.app.setWindowIcon, original_icon)
        self.addCleanup(self.app.setApplicationDisplayName, original_name)
        with tempfile.TemporaryDirectory() as directory:
            self.fc.getUserConfigDir = lambda: directory
            shell = boot.Shell(self.mw)
            # The host supplies the compiled SVG resource; use a real Qt icon here
            # so this standalone test checks propagation without FreeCAD's qrc.
            image = QtGui.QPixmap(20, 20)
            image.fill(QtGui.QColor("#526579"))
            shell.brand_icon = QtGui.QIcon(image)
            button = QtWidgets.QToolButton(self.mw)
            button.setText("File")
            shell.ribbon = types.SimpleNamespace(file_button=button)
            mdi = QtWidgets.QMdiArea(self.mw)
            self.mw.setCentralWidget(mdi)
            document = mdi.addSubWindow(QtWidgets.QWidget())
            shell.apply_branding()
            self.assertEqual(boot.PRISMA_ICON, ":/icons/prisma.svg")
            self.assertEqual(self.app.applicationDisplayName(), "PRISMA")
            expected = shell.brand_icon.cacheKey()
            for icon in (self.app.windowIcon(), self.mw.windowIcon(), document.windowIcon(), button.icon()):
                self.assertEqual(icon.cacheKey(), expected)
            self.assertEqual(button.text(), "File")
            self.assertEqual(button.toolButtonStyle(), QtCore.Qt.ToolButtonTextBesideIcon)
            self.assertEqual(button.accessibleName(), "PRISMA File")
            second_document = mdi.addSubWindow(QtWidgets.QWidget())
            second_document.setWindowIcon(QtGui.QIcon())
            shell.apply_branding()
            self.assertEqual(second_document.windowIcon().cacheKey(), expected)

    def test_imported_role_shortcut_runs_only_enabled_mapped_command_and_rebinds(self):
        boot = importlib.import_module(self.package_name + ".prisma.boot")
        with tempfile.TemporaryDirectory() as directory:
            self.fc.getUserConfigDir = lambda: directory
            shell = boot.Shell(self.mw)
            calls = []
            enabled = [True]
            command = types.SimpleNamespace(IsActive=lambda: enabled[0], Activated=lambda: calls.append("point"))
            shell.commands = types.SimpleNamespace(_commands={"Prisma_Point": command})
            shell.layout = {"shortcuts": [{"key": "Ctrl+Alt+P", "cmd": "Prisma_Point", "enabled": True},
                                           {"key": "Ctrl+Alt+Q", "cmd": None, "enabled": False}]}
            shell.install_role_shortcuts()
            self.assertEqual(len(shell.role_shortcuts), 1)
            self.mw.activateWindow()
            self.mw.setFocus()
            self.app.processEvents()
            QtTest.QTest.keyClick(self.mw, QtCore.Qt.Key_P, QtCore.Qt.ControlModifier | QtCore.Qt.AltModifier)
            self.app.processEvents()
            self.assertEqual(calls, ["point"])
            enabled[0] = False
            QtTest.QTest.keyClick(self.mw, QtCore.Qt.Key_P, QtCore.Qt.ControlModifier | QtCore.Qt.AltModifier)
            self.app.processEvents()
            self.assertEqual(calls, ["point"])
            shell.layout = {"shortcuts": []}
            shell.install_role_shortcuts()
            self.assertEqual(shell.role_shortcuts, [])
            shell.deleteLater()

    def test_imported_role_radial_retains_unavailable_slots_and_dispatches_known_commands(self):
        boot = importlib.import_module(self.package_name + ".prisma.boot")
        with tempfile.TemporaryDirectory() as directory:
            self.fc.getUserConfigDir = lambda: directory
            shell = boot.Shell(self.mw)
            calls = []
            command = types.SimpleNamespace(IsActive=lambda: True, Activated=lambda: calls.append("point"))
            shell.commands = types.SimpleNamespace(_commands={"Prisma_Point": command})
            shell.layout = {"radials": [{"name": "Imported", "items": [
                {"cmd": "Prisma_Point", "label": "Point", "enabled": True},
                {"cmd": None, "label": "NX-only Command", "enabled": False}]}]}
            items = shell.role_radial_items()
            self.assertEqual([item["label"] for item in items], ["Point", "NX-only Command"])
            self.assertTrue(items[0]["enabled"])
            self.assertFalse(items[1]["enabled"])
            items[0]["run"]()
            self.assertEqual(calls, ["point"])
            shell.deleteLater()

    def test_cylinder_reverse_is_available_in_both_construction_modes(self):
        self.command.begin("cylinder")
        for method in self.definitions.METHODS["cylinder"]:
            dialog = self.command.dialog
            dialog.method.setCurrentIndex(dialog.method.findData(method))
            dialog = self.command.dialog
            reverse = dialog.findChild(QtWidgets.QCheckBox, "reverse_direction")
            self.assertIsNotNone(reverse)
            reverse.setChecked(True)
            self.assertTrue(dialog.spec["reverse"])

    def test_associative_wcs_is_optional_and_restores_with_constructor_method(self):
        dialog = self.command.begin("point")
        self.assertFalse(dialog.associative_wcs.isEnabled())
        self.assertFalse(dialog.associative_wcs.isChecked())
        dialog.frame.setCurrentIndex(dialog.frame.findData("wcs"))
        self.assertTrue(dialog.associative_wcs.isEnabled())
        dialog.associative_wcs.setChecked(True)
        dialog.method.setCurrentIndex(dialog.method.findData("point_expression"))
        self.assertTrue(self.command.dialog.spec["associative_frame"])
        self.assertTrue(self.command.dialog.associative_wcs.isChecked())
        self.assertEqual(self.command.dialog.spec["frame"], "wcs")

    def test_collector_selection_layer_overrides_global_type_and_restores_after_nested_cancel(self):
        selection = importlib.import_module(self.package_name + ".selection_filter")
        selection._manager = None
        self.gui.Selection.addSelectionGate = lambda *args: None
        self.gui.Selection.removeSelectionGate = lambda: None
        self.gui.Control = types.SimpleNamespace(activeDialog=lambda: None)
        self.gui.ActiveDocument = types.SimpleNamespace(ActiveView=types.SimpleNamespace(getActiveObject=lambda *args: None))
        class Geometry:
            Name = Label = "Block"
            TypeId = "Prisma::Block"
            Shape = types.SimpleNamespace(Solids=[object()], isNull=lambda: False)
            def isDerivedFrom(self, type_id):
                return type_id in ("Part::Feature", "Part::Box")
            def getParentGeoFeatureGroup(self):
                return None
        self.doc.getObject = lambda name: Geometry()
        manager = selection.manager()
        manager.set_global(kind="edge")
        self.command.begin("block")
        self.assertTrue(manager.verdict("Model", "Block", "Face1")[0])
        self.command.open_constructor("origin")
        dialog = self.command.dialog
        dialog.method.setCurrentIndex(dialog.method.findData("existing"))
        self.assertFalse(manager.verdict("Model", "Block", "Face1")[0])
        self.assertTrue(manager.verdict("Model", "Block", "Vertex1")[0])
        self.command.cancel_current()
        self.assertTrue(manager.verdict("Model", "Block", "Face1")[0])
        self.command.cancel_current()
        self.assertIsNone(manager.top())
        self.assertFalse(manager.verdict("Model", "Block", "Face1")[0])
        del self.gui.Selection.addSelectionGate
        del self.gui.Selection.removeSelectionGate
        selection._manager = None

    def test_role_inheritance_preview_cancel_and_apply_leave_sources_untouched(self):
        boot = importlib.import_module(self.package_name + ".prisma.boot")
        with tempfile.TemporaryDirectory() as directory:
            role = Path(directory) / "user.mtx"
            base = Path(directory) / "base.mtx"
            role.write_text('<NX_PROFILES title="User Role" refRole="base.mtx"><Profile name="UG_APP_MODELING">'
                '<ActionList name="User"><ActionItem name="NX_UNKNOWN" label="Unavailable NX"/>'
                '</ActionList></Profile></NX_PROFILES>')
            base.write_text('<NX_PROFILES><Profile name="UG_APP_MODELING"><ActionList name="Base">'
                '<ActionItem name="POINT_FEATURE" label="Inherited Point"/></ActionList></Profile></NX_PROFILES>')
            original = (role.read_bytes(), base.read_bytes())
            self.fc.getUserConfigDir = lambda: directory
            shell = boot.Shell(self.mw)
            shell.layout = {"tabs": [], "name": "Keep this"}
            builds = []
            shell.build_ribbon = lambda: builds.append(shell.layout["name"])
            captured = []
            for accept in (False, True):
                def review(accept=accept):
                    dialog = self.app.activeModalWidget()
                    tree = dialog.findChild(QtWidgets.QTreeWidget)
                    text = dialog.findChild(QtWidgets.QPlainTextEdit).toPlainText()
                    captured.append((tree.topLevelItemCount(), text))
                    buttons = dialog.findChild(QtWidgets.QDialogButtonBox)
                    button = buttons.button(QtWidgets.QDialogButtonBox.Ok if accept else QtWidgets.QDialogButtonBox.Cancel)
                    QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
                QtCore.QTimer.singleShot(0, review)
                with patch.object(QtWidgets.QFileDialog, "getOpenFileName", side_effect=[(str(role), ""), (str(base), "")]):
                    shell.import_role()
                if not accept:
                    self.assertEqual(shell.layout["name"], "Keep this")
                    self.assertFalse(shell.layout_path.exists())
            self.assertEqual(builds, ["User Role"])
            self.assertTrue(shell.preferences["role_imported"])
            self.assertTrue(shell.layout_path.exists())
            self.assertEqual((role.read_bytes(), base.read_bytes()), original)
            self.assertTrue(all("NX_UNKNOWN" in text and count >= 3 for count, text in captured))
            labels = [item["label"] for tab in shell.layout["tabs"] for group in tab["groups"] for item in group["items"]]
            self.assertIn("Inherited Point", labels)
            self.assertIn("Unavailable NX", labels)
            shell.deleteLater()

    def test_raw_middle_drag_does_not_complete_command(self):
        self.command.begin("point")
        module, mouse, widget = self.navigation()
        with patch.dict(os.environ, {"PRISMA_PROFILE": "1"}):
            QtTest.QTest.mousePress(widget, QtCore.Qt.MiddleButton, pos=QtCore.QPoint(60, 60))
            move = QtGui.QMouseEvent(QtCore.QEvent.MouseMove, QtCore.QPointF(100, 90),
                                    QtCore.QPointF(100, 90), QtCore.Qt.NoButton,
                                    QtCore.Qt.MiddleButton, QtCore.Qt.NoModifier)
            QtWidgets.QApplication.sendEvent(widget, move)
            QtTest.QTest.mouseRelease(widget, QtCore.Qt.MiddleButton, pos=QtCore.QPoint(100, 90))
        self.assertTrue(self.command.session.active)
        self.assertEqual(self.adapter.features, [])
        widget.removeEventFilter(mouse)
        mouse.deleteLater()
        widget.deleteLater()


if __name__ == "__main__":
    unittest.main()
