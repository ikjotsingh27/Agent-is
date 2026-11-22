import unittest

from src.core.tools import LOAD_TOOL, ToolRegistry, builtin_tools


class ToolTests(unittest.TestCase):
    def test_nothing_enabled_means_no_catalog(self):
        reg = ToolRegistry(builtin_tools(), [])
        self.assertEqual(reg.catalog_text(), "")
        self.assertEqual(reg.names(), [])

    def test_catalog_is_short_and_lists_only_enabled_tools(self):
        reg = ToolRegistry(builtin_tools(), ["current_time", "not_a_tool"])
        text = reg.catalog_text()
        self.assertIn("current_time", text)
        self.assertNotIn("not_a_tool", text)
        self.assertNotIn("input_schema", text)  # no full definitions in the catalog

    def test_definition_and_run(self):
        reg = ToolRegistry(builtin_tools(), ["current_time"])
        self.assertEqual(reg.definition("current_time")["name"], "current_time")
        self.assertTrue(reg.run("current_time", {}))

    def test_disabled_tool_cannot_run(self):
        reg = ToolRegistry(builtin_tools(), [])
        with self.assertRaises(KeyError):
            reg.run("current_time", {})

    def test_load_tool_definition_is_tiny(self):
        self.assertLess(len(str(LOAD_TOOL)), 500)


if __name__ == "__main__":
    unittest.main()
