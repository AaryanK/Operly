import unittest

from packages.studio.design import compose_initial_site
from packages.studio.renderer import render_site
from packages.studio.schema import SiteSchema, blank_site


class StudioDesignQualityTests(unittest.TestCase):
    """Legacy renderer coverage.

    The category design planner is no longer the primary creation/edit engine;
    source-agent behavior is covered in test_studio_source_agent.py.
    """

    def test_legacy_renderer_still_handles_rich_schema_versions(self):
        site=compose_initial_site("Orbit Labs","AI software for modern operations.",["Automation","Analytics"],{})
        html=render_site(site,"home","orbit-demo")
        self.assertIn("data-operly-section-type=\"hero\"",html)
        self.assertIn("button secondary",html)
        self.assertIn("theme-dark",html)

    def test_blank_site_is_neutral_legacy_compatibility_only(self):
        site=blank_site("Example Studio","A modern service business.")
        self.assertIsInstance(site,SiteSchema)
        self.assertEqual(len(site.pages),1)
        self.assertEqual(len(site.pages[0].sections),1)
        self.assertEqual(site.pages[0].sections[0].id,"legacy-placeholder")
        self.assertEqual(site.theme.mode,"dark")


if __name__=="__main__":
    unittest.main()
