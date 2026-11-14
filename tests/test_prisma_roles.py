# SPDX-License-Identifier: LGPL-2.1-or-later
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "NXStyle"))
from nxstyle.prisma.roles import (SOLID_COMMANDS, default_layout, dismiss_role_import,
                                 import_role, read_role, should_offer_role_import,
                                 required_role_references, selected_role_sources, COMMAND_REGISTRY_VERSION)


class Roles(unittest.TestCase):
    def test_unknown_command_keeps_disabled_slot_and_actions_are_inert(self):
        result = import_role('''<NX_PROFILES><Profile name="UG_APP_MODELING"><Layout><ActionLists>
          <ActionList name="custom" type="toolbar"><ActionItem name="BLOCK_FEATURE"/>
          <ActionItem name="UNKNOWN_CAD" action="rm -rf /"/></ActionList>
          </ActionLists></Layout></Profile></NX_PROFILES>''')
        items = result.layout["tabs"][0]["groups"][-1]["items"]
        self.assertEqual(items[0]["cmd"], "Prisma_Block")
        self.assertEqual(items[1]["external_id"], "UNKNOWN_CAD")
        self.assertFalse(items[1]["enabled"])
        self.assertIsNone(items[1]["cmd"])
        self.assertTrue(any("Executable" in d for d in result.diagnostics))
        self.assertEqual(result.layout["registry_version"], COMMAND_REGISTRY_VERSION)

    def test_inheritance_requires_explicit_safe_source_and_rejects_cycle(self):
        result = import_role('<NX_PROFILES refRole="base.mtx" title="My Role"/>',
                             inherited={"base.mtx": '<NX_PROFILES title="Base"/>'})
        self.assertEqual(result.layout["name"], "My Role")
        result = import_role('<NX_PROFILES refRole="../secret.mtx"/>',
                             inherited={"../secret.mtx": '<NX_PROFILES title="Bad"/>'})
        self.assertTrue(any("Unsafe" in d for d in result.diagnostics))
        with self.assertRaisesRegex(ValueError, "cycle"):
            import_role('<NX_PROFILES refRole="a.mtx"/>',
                        inherited={"a.mtx": '<NX_PROFILES refRole="a.mtx"/>'})

    def test_entities_malformed_and_oversized_inputs_fail(self):
        for xml in ('<!DOCTYPE NX_PROFILES [<!ENTITY x "expand">]><NX_PROFILES>&x;</NX_PROFILES>',
                    '<!DOCTYPE NX_PROFILES SYSTEM "https://example.com/a"><NX_PROFILES/>',
                    '<NX_PROFILES>', '<not_a_role/>', '<NX_PROFILES/>'.encode('utf-16')):
            with self.subTest(xml=str(xml)[:50]), self.assertRaises(ValueError):
                import_role(xml)
        with self.assertRaises(ValueError):
            import_role(b" " * (4 * 1024 * 1024 + 1))

    def test_source_file_is_unchanged_and_default_is_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "role.mtx"
            content = b'<NX_PROFILES title="Mine"/>'
            path.write_bytes(content)
            read_role(path)
            self.assertEqual(path.read_bytes(), content)
        one = default_layout()
        one["tabs"].clear()
        self.assertTrue(default_layout()["tabs"])

    def test_optional_prompt_only_after_all_five_solid_commands_available(self):
        prefs = {}
        self.assertFalse(should_offer_role_import(prefs, {"Prisma_Block"}))
        self.assertTrue(should_offer_role_import(prefs, SOLID_COMMANDS))
        dismiss_role_import(prefs)
        self.assertFalse(should_offer_role_import(prefs, SOLID_COMMANDS))

    def test_shortcuts_are_normalized_conflicts_disabled_and_radial_slots_preserved(self):
        result = import_role('''<NX_PROFILES><Shortcuts>
          <Shortcut command="BLOCK_FEATURE" key="Control+Shift+b"/>
          <Shortcut command="CYLINDER_FEATURE" key="Ctrl+Shift+B"/>
          <Shortcut command="MISSING" key="Alt+F4"/>
          </Shortcuts><Profile name="UG_APP_MODELING"><Layout><ActionLists>
          <ActionList name="view_popup" type="radial"><ActionItem name="BLOCK_FEATURE"/>
          <ActionItem name="MISSING"/></ActionList></ActionLists></Layout></Profile></NX_PROFILES>''')
        bindings = result.layout["shortcuts"]
        self.assertEqual(bindings[0]["key"], "Ctrl+Shift+B")
        self.assertFalse(bindings[0]["enabled"])
        self.assertFalse(bindings[1]["enabled"])
        self.assertFalse(bindings[2]["enabled"])
        self.assertEqual(len(result.layout["radials"][0]["items"]), 2)
        self.assertTrue(any("conflicts" in d for d in result.diagnostics))

    def test_inherited_role_picker_only_reads_explicit_files_in_selected_roots(self):
        self.assertEqual(required_role_references('<NX_PROFILES refRole="base.mtx"/>'), ["base.mtx"])
        self.assertEqual(required_role_references('<NX_PROFILES refRole="https://example.com/role.mtx"/>'), [])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "selected"
            root.mkdir()
            inside = root / "base.mtx"
            inside.write_text('<NX_PROFILES title="Base"/>')
            outside = Path(tmp) / "outside.mtx"
            outside.write_text('<NX_PROFILES/>')
            sources = selected_role_sources([inside], [root])
            self.assertIn("base.mtx", sources)
            with self.assertRaisesRegex(ValueError, "outside"):
                selected_role_sources([outside], [root])
