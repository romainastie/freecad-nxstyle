# SPDX-License-Identifier: LGPL-2.1-or-later
import copy
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "NXStyle"))
from nxstyle.prisma import CommandSession, PreviewResult, command_spec


class Adapter:
    def __init__(self):
        self.objects = []
        self.fail = False

    def preview(self, spec):
        valid = spec.get("fields", {}).get("length", "1") != "0"
        return PreviewResult(valid, error="Length must be positive" if not valid else "")

    def commit(self, spec, editing=None):
        if self.fail:
            raise RuntimeError("Kernel refused the result")
        feature = copy.deepcopy(spec)
        if editing is not None:
            self.objects[self.objects.index(editing)] = feature
        else:
            self.objects.append(feature)
        return feature

    def load(self, feature):
        return copy.deepcopy(feature)


class Sessions(unittest.TestCase):
    def setUp(self):
        self.adapter = Adapter()
        self.session = CommandSession(self.adapter)

    def test_previews_and_nested_cancel_create_nothing(self):
        root = command_spec("block")
        self.session.begin(root)
        self.session.push("point", command_spec("point"), lambda value: root.update(origin=value))
        child = command_spec("point")
        child["fields"]["x"] = "15"
        self.session.update(child)
        self.session.pop(accepted=False)
        self.assertEqual(self.session.root["origin"]["fields"]["x"], "0")
        self.assertEqual(self.adapter.objects, [])
        self.session.cancel()
        self.assertFalse(self.session.open)

    def test_nested_accept_then_parent_cancel_does_not_commit(self):
        self.session.begin(command_spec("block"))
        self.session.push("point", command_spec("point"),
                          lambda value: self.session.root.update(origin=value))
        point = command_spec("point")
        point["fields"]["x"] = "20"
        self.session.pop(point)
        self.assertEqual(self.session.root["origin"]["fields"]["x"], "20")
        self.session.cancel()
        self.assertEqual(self.adapter.objects, [])

    def test_apply_then_cancel_retains_only_committed_operation(self):
        self.session.begin(command_spec("block"))
        first = self.session.apply()
        self.assertTrue(self.session.open)
        self.session.cancel()
        self.assertEqual(self.adapter.objects, [first])

    def test_invalid_inputs_and_commit_failure_leave_session_open(self):
        spec = command_spec("block")
        spec["fields"]["length"] = "0"
        self.session.begin(spec)
        with self.assertRaises(ValueError):
            self.session.accept()
        self.assertTrue(self.session.open)
        spec["fields"]["length"] = "10"
        self.session.update(spec)
        self.adapter.fail = True
        with self.assertRaisesRegex(RuntimeError, "Kernel refused"):
            self.session.accept()
        self.assertTrue(self.session.open)
        self.assertEqual(self.adapter.objects, [])

    def test_root_cannot_commit_while_child_is_active(self):
        self.session.begin(command_spec("cylinder"))
        self.session.push("vector", command_spec("vector"))
        with self.assertRaisesRegex(RuntimeError, "constructor"):
            self.session.apply()
        self.assertEqual(self.adapter.objects, [])

    def test_edit_apply_updates_existing_object(self):
        old = self.adapter.commit(command_spec("block"))
        self.session.begin(editing=old)
        spec = copy.deepcopy(self.session.root)
        spec["fields"]["length"] = "25"
        self.session.update(spec)
        self.session.apply()
        self.session.cancel()
        self.assertEqual(len(self.adapter.objects), 1)
        self.assertEqual(self.adapter.objects[0]["fields"]["length"], "25")
