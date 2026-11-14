# SPDX-License-Identifier: LGPL-2.1-or-later
"""Selection classification of actual native datums, including part-qualified picks."""
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "NXStyle"))


@unittest.skipIf(os.environ.get("PRISMA_TEST_NATIVE") == "0", "Explicit pure-test run")
class NativeDatumSelection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import FreeCAD
        import Part
        import Prisma
        from nxstyle import selection_filter
        from nxstyle.prisma import model
        cls.App, cls.selection, cls.model = FreeCAD, selection_filter, model

    def setUp(self):
        self.doc = self.model.new_document("PrismaSelectionTest")
        self.part = next(obj for obj in self.doc.Objects if getattr(obj, "PrismaModel", False))

    def tearDown(self):
        self.App.closeDocument(self.doc.Name)

    def test_whole_native_datum_and_part_occurrence_are_classified_as_geometry(self):
        for native_type, name, app_base in (("ConstructionPoint", "Point", "App::Point"),
                                            ("ConstructionAxis", "Axis", "App::Line"),
                                            ("ConstructionPlane", "Plane", "App::Plane")):
            with self.subTest(native_type=native_type):
                obj = self.doc.addObject("Prisma::" + native_type, name)
                self.part.addObject(obj)
                self.assertTrue(obj.isDerivedFrom(app_base))
                self.assertFalse(obj.isDerivedFrom("Part::Datum"))
                self.assertFalse(obj.isDerivedFrom("Part::Feature"))
                self.assertTrue(self.selection.type_ok("datum", obj, ""))
                if name == "Point":
                    self.assertTrue(self.selection.type_ok("vertex", obj, ""))
                for top, sub in ((obj, ""), (self.part, obj.Name + ".")):
                    pick = self.selection.Resolved(self.doc, top, sub)
                    self.assertIs(pick.leaf, obj)
                    self.assertEqual(pick.kind, "datum")
                    gate = self.selection.Layer("Constructor", kinds=("vertex", "edge", "face", "datum"))
                    self.assertEqual(gate.verdict(pick), (True, ""))


if __name__ == "__main__":
    unittest.main()
