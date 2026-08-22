import unittest

from packages.studio.design import compose_initial_site,infer_design_plan
from packages.studio.renderer import render_site
from packages.studio.schema import SiteSchema,blank_site


class StudioDesignQualityTests(unittest.TestCase):
    def test_travel_business_gets_composed_site_not_context_dump(self):
        raw=("ANHITRA — Antu Hill Travels NP Nepal trips. Flights. Tours. Vehicle hire. "
             "Facebook ANHITRA. Call now +977-1-4215126. Clear planning and reliable execution for Nepal travel.")
        site=compose_initial_site("ANHITRA",raw,["Nepal tours","Flights","Vehicle hire"],{"phones":["+977-1-4215126"]})
        page=site.pages[0]
        self.assertGreaterEqual(len(page.sections),7)
        hero=next(x for x in page.sections if x.type=="hero")
        self.assertNotIn("facebook",hero.props.description.lower())
        self.assertNotIn("+977",hero.props.description.lower())
        self.assertIn(hero.props.variant,{"immersive","spotlight","split","editorial","centered"})
        self.assertEqual(site.theme.mode,"dark")
        self.assertEqual(site.theme.visual_style,"editorial")
        self.assertTrue(any(x.type=="service_grid" for x in page.sections))
        self.assertTrue(any(x.type=="contact_form" for x in page.sections))

    def test_blank_site_is_a_complete_editable_first_draft(self):
        site=blank_site("Example Studio","A modern service business helping customers move from question to solution.")
        self.assertIsInstance(site,SiteSchema)
        kinds=[x.type for x in site.pages[0].sections]
        self.assertEqual(kinds[0],"navbar")
        self.assertIn("hero",kinds)
        self.assertIn("feature_grid",kinds)
        self.assertIn("cta",kinds)
        self.assertIn("contact_form",kinds)
        self.assertEqual(kinds[-1],"footer")

    def test_renderer_exposes_visual_variants_and_secondary_cta(self):
        site=compose_initial_site("Orbit Labs","AI software for modern operations.",["Automation","Analytics"],{})
        html=render_site(site,"home","orbit-demo")
        self.assertIn("data-operly-section-type=\"hero\"",html)
        self.assertIn("hero-orbit",html)
        self.assertIn("button secondary",html)
        self.assertIn("variant-bento",html)
        self.assertIn("theme-dark",html)
        self.assertIn("style-cosmic",html)

    def test_design_plan_changes_by_business_type(self):
        travel=infer_design_plan("Mountain Tours","Nepal trekking and travel",[])
        software=infer_design_plan("Orbit AI","software platform",[])
        legal=infer_design_plan("Kafle Legal","legal advisory",[])
        self.assertNotEqual(travel.visual_style,software.visual_style)
        self.assertNotEqual(software.grid_variant,legal.grid_variant)


if __name__=="__main__":
    unittest.main()
