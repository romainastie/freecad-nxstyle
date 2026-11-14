# SPDX-License-Identifier: LGPL-2.1-or-later
"""Native integration; only the explicit pure-test mode skips this suite."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "NXStyle"))
from nxstyle.prisma import command_spec, METHODS


@unittest.skipIf(os.environ.get("PRISMA_TEST_NATIVE") == "0", "Explicit pure-test run")
class NativeModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import FreeCAD
        import Part
        import Prisma
        from nxstyle.prisma import model
        cls.App, cls.Part, cls.native, cls.model = FreeCAD, Part, Prisma, model

    def setUp(self):
        self.doc = self.model.new_document("PrismaModelTest")
        self.adapter = self.model.ModelAdapter(self.doc)

    def tearDown(self):
        if self.doc.Name in self.App.listDocuments():
            self.App.closeDocument(self.doc.Name)

    @staticmethod
    def point(x=0, y=0, z=0):
        spec = command_spec("point")
        spec["fields"].update(x=str(x), y=str(y), z=str(z))
        return spec

    def block(self, x=0, y=0, z=0, length=10, width=10, height=10):
        spec = command_spec("block")
        spec["origin"] = self.point(x, y, z)
        spec["fields"].update(length=str(length), width=str(width), height=str(height))
        return spec

    def test_preview_has_no_document_artifacts_and_validates_required_origin(self):
        before = [o.Name for o in self.doc.Objects]
        for _ in range(3):
            result = self.adapter.preview(self.block())
            self.assertTrue(result.valid, result.error)
            self.assertAlmostEqual(result.shape.Volume, 1000)
        self.assertEqual([o.Name for o in self.doc.Objects], before)
        spec = self.block()
        spec["origin"]["_specified"] = False
        self.assertFalse(self.adapter.preview(spec).valid)
        self.assertEqual([o.Name for o in self.doc.Objects], before)

    def test_block_edit_native_expression_persistence_and_undo(self):
        feature = self.adapter.commit(self.block(length="0.5 in"))
        self.assertEqual(feature.TypeId, "Prisma::Block")
        self.assertAlmostEqual(feature.Length.Value, 12.7)
        count = len(self.doc.Objects)
        spec = self.adapter.load(feature)
        spec["fields"]["length"] = "25 mm"
        self.adapter.commit(spec, editing=feature)
        self.assertAlmostEqual(feature.Shape.Volume, 2500)
        self.assertEqual(len(self.doc.Objects), count)
        self.doc.undo()
        self.doc.recompute()
        self.assertAlmostEqual(feature.Length.Value, 12.7)
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "model.FCStd")
            self.doc.saveAs(path)
            name = self.doc.Name
            feature_name = feature.Name
            self.App.closeDocument(name)
            self.doc = self.App.openDocument(path)
            loaded = self.model.ModelAdapter(self.doc).load(self.doc.getObject(feature_name))
            self.assertEqual(loaded["method"], "origin_lengths")

    def test_associative_two_point_block_follows_parent_and_restores_method(self):
        first = self.adapter.commit(self.point(0, 0, 0))
        second = self.adapter.commit(self.point(10, 20, 30))
        spec = command_spec("block", "diagonal_points")
        for key, obj in (("origin", first), ("point2", second)):
            ref = command_spec("point", "existing")
            ref["references"]["object"] = [{"object": obj.Name}]
            spec[key] = ref
        feature = self.adapter.commit(spec)
        self.assertAlmostEqual(feature.Shape.Volume, 6000)
        second.setExpression("X", "20 mm")
        self.doc.recompute()
        self.assertAlmostEqual(feature.Shape.Volume, 12000)
        loaded = self.adapter.load(feature)
        self.assertEqual(loaded["method"], "diagonal_points")
        self.assertEqual(loaded["point2"]["references"]["object"][0]["object"], second.Name)

    def test_inferred_whole_points_are_associative_block_origins(self):
        native = self.adapter.commit(self.point(7,8,9))
        app_point = self.doc.addObject("App::Point","AppPoint")
        app_point.Placement.Base = self.App.Vector(4,5,6)
        imported = self.doc.addObject("Part::Feature","ImportedPoint")
        imported.Shape = self.Part.Point(self.App.Vector(10,20,30)).toShape()
        for source in (native,app_point,imported):
            with self.subTest(type=source.TypeId):
                inferred = command_spec("point","inferred")
                inferred["references"]["object"] = [{"object":source.Name}]
                expected = self.Part.getShape(source,needSubElement=True).Vertexes[0].Point
                before = len(self.doc.Objects)
                preview = self.adapter.preview(inferred)
                self.assertTrue(preview.valid,preview.error)
                self.assertLess((self.App.Vector(*preview.values["point"])-expected).Length,1e-7)
                spec = self.block()
                spec["origin"] = inferred
                self.assertTrue(self.adapter.preview(spec).valid)
                self.assertEqual(len(self.doc.Objects),before)
                block = self.adapter.commit(spec)
                if source is native:
                    source.setExpression("X","17 mm")
                elif source is app_point:
                    source.Placement.Base = self.App.Vector(14,5,6)
                else:
                    source.Shape = self.Part.Point(self.App.Vector(20,20,30)).toShape()
                self.doc.recompute()
                expected = self.Part.getShape(source,needSubElement=True).Vertexes[0].Point
                self.assertLess((block.Placement.Base-expected).Length,1e-7)
                loaded = self.adapter.load(block)["origin"]
                self.assertEqual(loaded["method"],"inferred")
                self.assertEqual(loaded["references"]["object"][0]["object"],source.Name)

    def test_qualified_datum_points_support_block_and_three_point_plane(self):
        part = self.model._part(self.doc)
        part.Placement = self.App.Placement(self.App.Vector(100,200,300),
                                            self.App.Rotation(self.App.Vector(0,0,1),90))
        inner = self.doc.addObject("App::Part","NestedDatums")
        part.addObject(inner)
        inner.Placement = self.App.Placement(self.App.Vector(5,6,7),
                                             self.App.Rotation(self.App.Vector(0,0,1),90))
        points = []
        for x,y in ((0,0),(10,0),(0,10)):
            point = self.doc.addObject("Prisma::ConstructionPoint","DatumPoint")
            inner.addObject(point)
            point.X,point.Y = x,y
            points.append(point)
        self.doc.recompute()
        definitions = []
        for point in points:
            spec = command_spec("point","inferred")
            spec["references"]["object"] = [{"object":part.Name,
                "subelement":inner.Name+"."+point.Name+"."}]
            result = self.adapter.preview(spec)
            self.assertTrue(result.valid,result.error)
            self.assertLess((self.App.Vector(*result.values["point"])-point.getGlobalPlacement().Base).Length,1e-7)
            definitions.append(spec)
        block = self.block()
        block["origin"] = definitions[0]
        solid = self.adapter.commit(block)
        plane = command_spec("plane","three_points")
        for key,definition in zip(("point1","point2","point3"),definitions):
            plane[key] = definition
        preview = self.adapter.preview(plane)
        self.assertTrue(preview.valid,preview.error)
        datum = self.adapter.commit(plane)
        self.assertTrue(preview.placement.isSame(datum.Placement,1e-7))
        points[0].Z = 2
        self.doc.recompute()
        expected = part.getGlobalPlacement().inverse().multiply(points[0].getGlobalPlacement()).Base
        for feature in (solid,datum):
            self.assertEqual(feature.Diagnostic,"")
            self.assertLess((feature.Placement.Base-expected).Length,1e-7)
        restored = self.adapter.load(solid)["origin"]["references"]["object"][0]
        self.assertEqual(restored["object"],part.Name)
        self.assertEqual(restored["subelement"],inner.Name+"."+points[0].Name+".")

    def test_qualified_axis_and_linked_point_inference_use_native_geometry(self):
        part = self.model._part(self.doc)
        inner = self.doc.addObject("App::Part","NestedFrame")
        part.addObject(inner)
        inner.Placement = self.App.Placement(self.App.Vector(2,3,4),
                                             self.App.Rotation(self.App.Vector(0,0,1),90))
        axis = self.doc.addObject("Prisma::ConstructionAxis","DatumAxis")
        inner.addObject(axis)
        axis.Direction = self.App.Vector(1,0,0)
        point = self.adapter.commit(self.point(1,2,3))
        link = self.doc.addObject("App::Link","PointOccurrence")
        part.addObject(link)
        link.LinkedObject = point
        link.Placement.Base = self.App.Vector(30,40,50)
        self.doc.recompute()
        for kind,source,sub,native_method in (("vector",part,inner.Name+"."+axis.Name+".","Edge"),
                                               ("point",link,"","Vertex")):
            with self.subTest(kind=kind):
                spec = command_spec(kind,"inferred")
                spec["references"]["object"] = [{"object":source.Name,"subelement":sub}]
                result = self.adapter.preview(spec)
                self.assertTrue(result.valid,result.error)
                expected = self.native.evaluate("Axis" if kind=="vector" else "Point",
                    {"Method":native_method,"Support":[(source,[sub])],"Context":part.getGlobalPlacement()})
                self.assertTrue(result.placement.isSame(expected,1e-7))

    def test_inline_subtract_matches_standalone_and_preserves_tool_option(self):
        target = self.adapter.commit(self.block())
        tool = self.adapter.commit(self.block(5, 0, 0))
        spec = command_spec("subtract")
        spec["references"] = {"target": [{"object": target.Name}], "tools": [{"object": tool.Name}]}
        spec["keep_tool"] = True
        separate = self.adapter.commit(spec)
        self.assertAlmostEqual(separate.Shape.Volume, 500)
        self.assertIn(tool, self.model.terminal_outputs(self.doc))
        other = self.adapter.commit(self.block(0, 20, 0))
        inline = self.block(5, 20, 0)
        inline.update(boolean="subtract", target=[{"object": other.Name}])
        result = self.adapter.commit(inline)
        self.assertAlmostEqual(result.Shape.Volume, separate.Shape.Volume)
        self.assertNotIn(other, self.model.terminal_outputs(self.doc))

    def test_retention_edits_restore_original_inputs_and_inline_retargets(self):
        target = self.adapter.commit(self.block())
        tool = self.adapter.commit(self.block(5, 0, 0))
        spec = command_spec("subtract")
        spec["references"] = {"target": [{"object": target.Name}], "tools": [{"object": tool.Name}]}
        result = self.adapter.commit(spec)
        self.assertEqual(self.model.terminal_outputs(self.doc), [result])
        retained = self.adapter.load(result)
        retained.update(keep_target=True, keep_tool=True)
        self.adapter.commit(retained, editing=result)
        self.assertEqual(set(o.Name for o in self.model.terminal_outputs(self.doc)),
                         {target.Name, tool.Name, result.Name})
        if target.ViewObject is not None:
            self.assertTrue(target.ViewObject.Visibility)
            self.assertTrue(tool.ViewObject.Visibility)
        first = self.adapter.commit(self.block(0, 20, 0))
        second = self.adapter.commit(self.block(0, 20, 0, length=15))
        inline = self.block(5, 20, 0)
        inline.update(boolean="subtract", target=[{"object": first.Name}])
        cut = self.adapter.commit(inline)
        changed = self.adapter.load(cut)
        changed["target"] = [{"object": second.Name}]
        self.adapter.commit(changed, editing=cut)
        self.assertEqual(cut.Base, second)
        self.assertIn(first, self.model.terminal_outputs(self.doc))
        self.assertNotIn(second, self.model.terminal_outputs(self.doc))

    def test_commit_exception_rolls_back_helpers_outputs_and_undo(self):
        before = [o.Name for o in self.doc.Objects]
        undo = list(self.doc.UndoNames)
        create = self.adapter._create
        def fail(spec, *args, **kwargs):
            obj = create(spec, *args, **kwargs)
            if spec["kind"] == "block":
                raise RuntimeError("Injected failure after native construction")
            return obj
        self.adapter._create = fail
        with self.assertRaisesRegex(RuntimeError, "Injected failure"):
            self.adapter.commit(self.block())
        self.assertEqual([o.Name for o in self.doc.Objects], before)
        self.assertEqual(self.model.terminal_outputs(self.doc), [])
        self.assertEqual(list(self.doc.UndoNames), undo)

    def test_wcs_capture_does_not_move_existing_points(self):
        self.model.set_wcs(self.doc, self.App.Placement(self.App.Vector(10, 0, 0), self.App.Rotation()))
        self.adapter.begin_context()
        spec = self.point(2, 0, 0)
        spec["frame"] = "wcs"
        feature = self.adapter.commit(spec)
        self.assertAlmostEqual(feature.Placement.Base.x, 12)
        self.model.set_wcs(self.doc, self.App.Placement(self.App.Vector(50, 0, 0), self.App.Rotation()))
        self.doc.recompute()
        self.assertAlmostEqual(feature.Placement.Base.x, 12)
        loaded = self.adapter.load(feature)
        self.assertAlmostEqual(self.adapter.preview(loaded).placement.Base.x, 12)
        self.model.set_wcs(self.doc, self.App.Placement(self.App.Vector(), self.App.Rotation(self.App.Vector(0, 0, 1), 30)))
        self.adapter.begin_context()
        spec = self.block()
        spec.update(frame="wcs", associative_frame=True)
        block = self.adapter.commit(spec)
        self.assertIsNotNone(block.FrameSupport)
        self.model.set_wcs(self.doc, self.App.Placement(self.App.Vector(), self.App.Rotation(self.App.Vector(0, 0, 1), 70)))
        self.doc.recompute()
        x_axis = block.Placement.Rotation.multVec(self.App.Vector(1, 0, 0))
        import math
        self.assertAlmostEqual(x_axis.x, math.cos(math.radians(70)))
        self.assertAlmostEqual(x_axis.y, math.sin(math.radians(70)))

    def test_coordinate_modes_offsets_and_reference_recipe_survive_reopen(self):
        spec = self.point("10 mm", "90 deg", "0 deg")
        spec["coordinate_mode"] = "spherical"
        spec["offset"] = {"method": "cylindrical", "values": ["5 mm", "90 deg", "1 mm"]}
        preview = self.adapter.preview(spec)
        self.assertTrue(preview.valid, preview.error)
        feature = self.adapter.commit(spec)
        self.assertAlmostEqual(feature.Placement.Base.x, 0, places=7)
        self.assertAlmostEqual(feature.Placement.Base.y, 15)
        self.assertAlmostEqual(feature.Placement.Base.z, 1)
        loaded = self.adapter.load(feature)
        self.assertEqual(loaded["coordinate_mode"], "spherical")
        self.assertEqual(loaded["offset"]["method"], "cylindrical")
        self.assertAlmostEqual(self.adapter.preview(loaded).placement.Base.y, 15)

    def test_nested_point_direction_and_plane_dependencies_remain_native(self):
        a = self.adapter.commit(self.point(1, 2, 3))
        b = self.adapter.commit(self.point(1, 2, 8))
        direction = command_spec("vector", "two_points")
        for key, obj in (("point1", a), ("point2", b)):
            value = command_spec("point", "existing")
            value["references"]["object"] = [{"object": obj.Name}]
            direction[key] = value
        plane = command_spec("plane", "point_normal")
        plane["point"] = self.point(0, 0, 4)
        plane["normal"] = direction
        result = self.adapter.commit(plane)
        self.assertEqual(result.TypeId, "Prisma::ConstructionPlane")
        self.assertAlmostEqual(result.Placement.Base.z, 4)
        loaded = self.adapter.load(result)
        self.assertEqual(loaded["normal"]["method"], "two_points")
        self.assertEqual(loaded["normal"]["point2"]["references"]["object"][0]["object"], b.Name)
        b.setExpression("X", "6 mm")
        self.doc.recompute()
        normal = result.Placement.Rotation.multVec(self.App.Vector(0, 0, 1))
        self.assertGreater(normal.x, 0.5)

    def test_every_ui_method_reaches_native_evaluator_without_document_mutation(self):
        def shape(name, value):
            obj = self.doc.addObject("Part::Feature", name)
            obj.Shape = value
            return obj
        box = shape("FixtureBox", self.Part.makeBox(10, 10, 10))
        cylinder = shape("FixtureCylinder", self.Part.makeCylinder(10, 20))
        circle = shape("FixtureCircle", self.Part.makeCircle(10))
        line_x = shape("FixtureX", self.Part.makeLine(self.App.Vector(-10, 0, 0), self.App.Vector(10, 0, 0)))
        line_y = shape("FixtureY", self.Part.makeLine(self.App.Vector(0, -10, 0), self.App.Vector(0, 10, 0)))
        self.doc.recompute()
        def ref(obj, sub=""):
            return {"object": obj.Name, "subelement": sub}
        before = [obj.Name for obj in self.doc.Objects]
        for kind in ("point", "vector", "axis", "plane", "block", "cylinder"):
            for method in METHODS[kind]:
                spec = command_spec(kind, method)
                refs = spec["references"]
                if "object" in refs:
                    refs["object"] = [ref(line_x)]
                if method in ("existing", "inferred"):
                    refs["object"] = [ref(box, "Vertex1") if kind == "point" else ref(box, "Face1")]
                if method in ("center", "quadrant", "angle_on_arc", "on_curve"):
                    refs["object"] = [ref(circle)]
                if method in ("on_face", "face_normal", "coincident", "at_distance", "at_angle"):
                    refs["object"] = [ref(box, "Face1")]
                if method == "curve_face_axis":
                    refs["object"] = [ref(cylinder, "Face1")]
                if method in ("intersection", "bisector"):
                    refs["objects"] = ([ref(line_x), ref(line_y)] if kind == "point" or kind == "axis" and method == "bisector"
                                       else [ref(box, "Face1"), ref(box, "Face3")])
                if method in ("two_lines", "curves_points"):
                    refs["objects"] = [ref(line_x), ref(line_y)]
                if method == "at_angle":
                    refs["axis"] = [ref(line_x)]
                if method == "tangent":
                    refs["object"] = [ref(cylinder, "Face1")]
                    spec["point"] = self.point(10, 0, 5)
                if "point2" in spec:
                    spec["point2"] = self.point(10, 20, 30)
                if "point3" in spec:
                    spec["point3"] = self.point(20, 0, 0)
                if method == "arc_height":
                    refs["arc"] = [ref(circle)]
                with self.subTest(kind=kind, method=method):
                    result = self.adapter.preview(spec)
                    self.assertTrue(result.valid, result.error)
                    self.assertEqual([obj.Name for obj in self.doc.Objects], before)

    def test_all_five_solid_commands_have_correct_volumes_and_editable_native_history(self):
        import math
        cylinder = command_spec("cylinder")
        cylinder["origin"] = self.point(30, 0, 0)
        cylinder["fields"].update(diameter="10 mm", height="20 mm")
        result = self.adapter.commit(cylinder)
        self.assertEqual(result.TypeId, "Prisma::Cylinder")
        self.assertAlmostEqual(result.Shape.Volume, math.pi * 500)
        for operation, expected in (("unite", 1500), ("subtract", 500), ("intersect", 500)):
            with self.subTest(operation=operation):
                target = self.adapter.commit(self.block())
                tool = self.adapter.commit(self.block(5, 0, 0))
                spec = command_spec(operation)
                spec["references"] = {"target": [{"object": target.Name}], "tools": [{"object": tool.Name}]}
                preview = self.adapter.preview(spec)
                self.assertTrue(preview.valid, preview.error)
                result = self.adapter.commit(spec)
                self.assertAlmostEqual(result.Shape.Volume, expected)
                self.assertEqual(result.Base, target)
                self.assertEqual(result.Tool, tool)
                self.assertEqual(self.adapter.load(result)["kind"], operation)

    def test_block_modes_and_arc_cylinder_keep_native_dependencies(self):
        import math
        for method, expected in (("two_points_height", 6000), ("diagonal_points", 6000)):
            with self.subTest(method=method):
                spec = command_spec("block", method)
                spec["origin"] = self.point(10, 20, 30)
                spec["point2"] = self.point(0, 0, 0)
                if method == "two_points_height":
                    spec["fields"]["height"] = "30 mm"
                preview = self.adapter.preview(spec)
                self.assertTrue(preview.valid, preview.error)
                feature = self.adapter.commit(spec)
                self.assertAlmostEqual(feature.Shape.Volume, expected)
                self.assertAlmostEqual(feature.Shape.Volume, preview.shape.Volume)
                self.assertEqual(self.adapter.load(feature)["method"], method)
        circle = self.doc.addObject("Part::Feature", "CylinderArc")
        circle.Shape = self.Part.makeCircle(5, self.App.Vector(30, 0, 0))
        spec = command_spec("cylinder", "arc_height")
        spec["references"]["arc"] = [{"object": circle.Name}]
        spec["fields"]["height"] = "20 mm"
        feature = self.adapter.commit(spec)
        self.assertAlmostEqual(feature.Shape.Volume, math.pi * 500)
        circle.Shape = self.Part.makeCircle(10, self.App.Vector(40, 0, 0))
        self.doc.recompute()
        self.assertAlmostEqual(feature.Shape.Volume, math.pi * 2000)
        self.assertAlmostEqual(feature.Placement.Base.x, 40)
        self.assertEqual(self.adapter.load(feature)["references"]["arc"][0]["object"], circle.Name)

    def test_boolean_invalid_results_and_foreign_ownership_leave_document_unchanged(self):
        target = self.adapter.commit(self.block())
        outside = self.adapter.commit(self.block(30, 0, 0))
        inside = self.adapter.commit(self.block(1, 1, 1, length=2, width=2, height=2))
        foreign = self.doc.addObject("Part::Feature", "OutsideModel")
        foreign.Shape = self.Part.makeBox(10, 10, 10, self.App.Vector(5, 0, 0))
        other_model = self.doc.addObject("App::Part", "OtherModel")
        other_model.addObject(foreign)
        compound = self.doc.addObject("Part::Feature", "CompoundInput")
        compound.Shape = self.Part.makeCompound([target.Shape, outside.Shape])
        self.model._part(self.doc).addObject(compound)
        self.doc.recompute()
        cases = [("unite", outside), ("subtract", outside), ("intersect", outside),
                 ("subtract", target), ("subtract", foreign), ("unite", compound)]
        for operation, tool in cases:
            with self.subTest(operation=operation, tool=tool.Name):
                spec = command_spec(operation)
                spec["references"] = {"target": [{"object": target.Name}], "tools": [{"object": tool.Name}]}
                before = [o.Name for o in self.doc.Objects]
                outputs = self.model.terminal_outputs(self.doc)
                undo = list(self.doc.UndoNames)
                self.assertFalse(self.adapter.preview(spec).valid)
                with self.assertRaises(ValueError):
                    self.adapter.commit(spec)
                self.assertEqual([o.Name for o in self.doc.Objects], before)
                self.assertEqual(self.model.terminal_outputs(self.doc), outputs)
                self.assertEqual(list(self.doc.UndoNames), undo)
        spec["references"]["tools"] = [{"object": outside.Name}, {"object": inside.Name}]
        self.assertIn("exactly one", self.adapter.preview(spec).error)
        contained = command_spec("unite")
        contained["references"] = {"target": [{"object": target.Name}], "tools": [{"object": inside.Name}]}
        self.assertTrue(self.adapter.preview(contained).valid)

    def test_dimension_mismatch_is_rejected_in_preview_and_commit(self):
        specs = []
        invalid_length = self.block(length="20 deg")
        specs.append(invalid_length)
        specs.append(self.point("5 deg", 0, 0))
        angle = command_spec("vector", "at_angle_to_xc")
        angle["fields"]["angle"] = "10 mm"
        specs.append(angle)
        for spec in specs:
            with self.subTest(kind=spec["kind"]):
                before = [o.Name for o in self.doc.Objects]
                result = self.adapter.preview(spec)
                self.assertFalse(result.valid)
                self.assertIn("expression", result.error)
                with self.assertRaises(ValueError):
                    self.adapter.commit(spec)
                self.assertEqual([o.Name for o in self.doc.Objects], before)

    def test_step_roundtrip_preserves_exported_terminal_geometry(self):
        target = self.adapter.commit(self.block())
        tool = self.adapter.commit(self.block(5, 0, 0))
        spec = command_spec("subtract")
        spec["references"] = {"target": [{"object": target.Name}], "tools": [{"object": tool.Name}]}
        result = self.adapter.commit(spec)
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "result.step")
            result.Shape.exportStep(path)
            restored = self.Part.Shape()
            restored.read(path)
            self.assertTrue(restored.isValid())
            self.assertEqual(len(restored.Solids), 1)
            self.assertAlmostEqual(restored.Volume, result.Shape.Volume, places=6)

    def test_loading_uses_canonical_native_reference_and_nonassociative_snapshot(self):
        source = self.adapter.commit(self.point(10, 20, 30))
        spec = command_spec("point", "existing")
        spec["references"]["object"] = [{"object": source.Name}]
        associative = self.adapter.commit(spec)
        replacement = self.adapter.commit(self.point(40, 50, 60))
        associative.Support = [(replacement, [""])]
        self.doc.recompute()
        loaded = self.adapter.load(associative)
        self.assertEqual(loaded["references"]["object"][0]["object"], replacement.Name)
        self.assertAlmostEqual(self.adapter.preview(loaded).placement.Base.x, 40)
        spec["associative"] = False
        snapshot = self.adapter.commit(spec)
        self.doc.removeObject(source.Name)
        self.doc.recompute()
        loaded = self.adapter.load(snapshot)
        result = self.adapter.preview(loaded)
        self.assertTrue(result.valid, result.error)
        self.assertEqual(loaded["method"], "existing")
        self.assertAlmostEqual(result.placement.Base.x, 10)
        self.adapter.commit(loaded, editing=snapshot)
        self.assertAlmostEqual(snapshot.Placement.Base.x, 10)

    def test_preview_world_transform_and_import_adoption_preserve_world_geometry(self):
        part = self.model._part(self.doc)
        part.Placement = self.App.Placement(self.App.Vector(100, 20, 30), self.App.Rotation(self.App.Vector(0, 0, 1), 90))
        self.doc.recompute()
        spec = self.block(5, 0, 0)
        preview = self.adapter.preview(spec)
        self.assertTrue(preview.valid, preview.error)
        committed = self.adapter.commit(spec)
        world = committed.Shape.copy()
        world.Placement = part.getGlobalPlacement().multiply(world.Placement)
        for axis in ("XMin", "YMin", "ZMin", "XMax", "YMax", "ZMax"):
            self.assertAlmostEqual(getattr(preview.shape.BoundBox, axis), getattr(world.BoundBox, axis))
        imported = self.doc.addObject("Part::Feature", "ImportedSolid")
        imported.Shape = self.Part.makeBox(10, 10, 10, self.App.Vector(10, 0, 0))
        before = imported.getGlobalPlacement()
        boolean = command_spec("unite")
        boolean["references"] = {"target": [{"object": committed.Name}], "tools": [{"object": imported.Name}]}
        preview = self.adapter.preview(boolean)
        self.assertTrue(preview.valid, preview.error)
        self.assertIsNone(imported.getParentGeoFeatureGroup())
        result = self.adapter.commit(boolean)
        self.assertEqual(imported.getParentGeoFeatureGroup(), part)
        self.assertTrue(imported.getGlobalPlacement().isSame(before, 1e-7))
        world = result.Shape.copy()
        world.Placement = part.getGlobalPlacement().multiply(world.Placement)
        self.assertAlmostEqual(preview.shape.Volume, world.Volume)
        self.assertAlmostEqual(preview.shape.BoundBox.XMin, world.BoundBox.XMin)
        self.doc.undo()
        self.doc.recompute()
        self.assertIsNone(imported.getParentGeoFeatureGroup())
        self.assertTrue(imported.getGlobalPlacement().isSame(before, 1e-7))
        self.assertEqual(self.model.terminal_outputs(self.doc), [committed])
        circle = self.doc.addObject("Part::Feature", "SeedCircle")
        circle.Shape = self.Part.makeCircle(10)
        line = self.doc.addObject("Part::Feature", "SeedLine")
        line.Shape = self.Part.makeLine(self.App.Vector(-20, 0, 0), self.App.Vector(20, 0, 0))
        part.addObject(circle)
        part.addObject(line)
        seed = part.getGlobalPlacement().multVec(self.App.Vector(-10, 0, 0))
        intersection = command_spec("point", "intersection")
        intersection["references"]["objects"] = [{"object": circle.Name, "point": list(seed)}, {"object": line.Name}]
        preview = self.adapter.preview(intersection)
        self.assertTrue(preview.valid, preview.error)
        self.assertAlmostEqual(preview.placement.Base.x, -10)
        self.model.set_wcs(self.doc, self.App.Placement(self.App.Vector(15, 25, 35), self.App.Rotation(self.App.Vector(1, 0, 0), 90)))
        self.adapter.begin_context()
        for kind, method in (("vector", "view_direction"), ("plane", "view_plane")):
            view = command_spec(kind, method)
            view.update(frame="wcs", view_direction=[0, 1, 0], view_point=[3, 4, 5])
            preview = self.adapter.preview(view)
            self.assertTrue(preview.valid, preview.error)
            self.assertAlmostEqual(preview.values["vector"][1], 1)
            if kind == "plane":
                for actual, expected in zip(preview.values["point"], (3, 4, 5)):
                    self.assertAlmostEqual(actual, expected)

    def test_cylinder_reverse_both_modes_persists_and_tracks_arc(self):
        for method in ("axis_diameter_height", "arc_height"):
            with self.subTest(method=method):
                spec = command_spec("cylinder", method)
                spec["fields"]["height"] = "20 mm"
                spec["reverse"] = True
                if method == "arc_height":
                    circle = self.doc.addObject("Part::Feature", "ReverseCylinderArc")
                    circle.Shape = self.Part.makeCircle(5)
                    spec["references"]["arc"] = [{"object": circle.Name}]
                preview = self.adapter.preview(spec)
                self.assertTrue(preview.valid, preview.error)
                self.assertAlmostEqual(preview.shape.BoundBox.ZMin, -20)
                feature = self.adapter.commit(spec)
                self.assertAlmostEqual(feature.Shape.BoundBox.ZMin, -20)
                if method == "arc_height":
                    circle.Shape = self.Part.makeCircle(5, self.App.Vector(0, 0, 10))
                    self.doc.recompute()
                    self.assertAlmostEqual(feature.Shape.BoundBox.ZMin, -10)
                with tempfile.TemporaryDirectory() as tmp:
                    path = str(Path(tmp) / "reverse.FCStd")
                    self.doc.saveAs(path)
                    name, feature_name = self.doc.Name, feature.Name
                    self.App.closeDocument(name)
                    self.doc = self.App.openDocument(path)
                    self.adapter = self.model.ModelAdapter(self.doc)
                    feature = self.doc.getObject(feature_name)
                    loaded = self.adapter.load(feature)
                    self.assertTrue(loaded["reverse"])
                    self.assertEqual(loaded["method"], method)
                    self.assertAlmostEqual(self.adapter.preview(loaded).shape.BoundBox.ZMin, feature.Shape.BoundBox.ZMin)

    def test_edit_rejects_self_and_descendant_dependencies_before_transaction(self):
        point = self.adapter.commit(self.point(1, 2, 3))
        dependent = command_spec("point", "existing")
        dependent["references"]["object"] = [{"object": point.Name}]
        child = self.adapter.commit(dependent)
        before = [obj.Name for obj in self.doc.Objects]
        undo = list(self.doc.UndoNames)
        for source in (point, child):
            with self.subTest(source=source.Name):
                cyclic = command_spec("point", "existing")
                cyclic["references"]["object"] = [{"object": source.Name}]
                with self.assertRaisesRegex(ValueError, "circular dependency"):
                    self.adapter.commit(cyclic, editing=point)
                self.assertEqual([obj.Name for obj in self.doc.Objects], before)
                self.assertEqual(list(self.doc.UndoNames), undo)
                self.assertEqual(point.Support, [])
        block = self.adapter.commit(self.block())
        nested = self.adapter.load(block)
        nested["origin"] = command_spec("point", "existing")
        nested["origin"]["references"]["object"] = [{"object": block.Name, "subelement": "Vertex1"}]
        with self.assertRaisesRegex(ValueError, "circular dependency"):
            self.adapter.commit(nested, editing=block)
        tool = self.adapter.commit(self.block(5, 0, 0))
        boolean = command_spec("subtract")
        boolean["references"] = {"target": [{"object": block.Name}], "tools": [{"object": tool.Name}]}
        cut = self.adapter.commit(boolean)
        changed = self.adapter.load(cut)
        changed["references"]["target"] = [{"object": cut.Name}]
        with self.assertRaisesRegex(ValueError, "circular dependency"):
            self.adapter.commit(changed, editing=cut)
