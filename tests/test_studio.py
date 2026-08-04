import json, unittest
from pydantic import ValidationError
from packages.studio.schema import SiteSchema, blank_site
from packages.studio.renderer import render_site, safe_url

class SchemaTests(unittest.TestCase):
    def test_valid(self): self.assertEqual(blank_site("Acme").schema_version,1)
    def test_unknown_component(self):
        d=blank_site("A").model_dump();d["pages"][0]["sections"][0]["type"]="script"
        with self.assertRaises(ValidationError):SiteSchema.model_validate(d)
    def test_unknown_property(self):
        d=blank_site("A").model_dump();d["pages"][0]["sections"][0]["props"]["html"]="x"
        with self.assertRaises(ValidationError):SiteSchema.model_validate(d)
    def test_script_injection(self):
        d=blank_site("A").model_dump();d["pages"][0]["sections"][0]["props"]["headline"]="<script>alert(1)</script>"
        with self.assertRaises(ValidationError):SiteSchema.model_validate(d)
    def test_dangerous_url(self):
        d=blank_site("A").model_dump();d["pages"][0]["sections"]=[{"id":"x","type":"image","props":{"url":"javascript:alert(1)","alt_text":"x"}}]
        with self.assertRaises(ValidationError):SiteSchema.model_validate(d)
    def test_duplicate_slug(self):
        d=blank_site("A").model_dump();d["pages"].append(d["pages"][0].copy())
        with self.assertRaises(ValidationError):SiteSchema.model_validate(d)
    def test_duplicate_section(self):
        d=blank_site("A").model_dump();d["pages"][0]["sections"].append(d["pages"][0]["sections"][0].copy())
        with self.assertRaises(ValidationError):SiteSchema.model_validate(d)
    def test_page_limit(self):
        d=blank_site("A").model_dump();d["pages"]=[{**d["pages"][0],"id":f"p{i}","slug":f"p{i}","sections":[]} for i in range(21)]
        with self.assertRaises(ValidationError):SiteSchema.model_validate(d)
    def test_section_limit(self):
        d=blank_site("A").model_dump();s=d["pages"][0]["sections"][0];d["pages"][0]["sections"]=[{**s,"id":f"s{i}"} for i in range(51)]
        with self.assertRaises(ValidationError):SiteSchema.model_validate(d)
class RendererTests(unittest.TestCase):
    def test_escaping_and_determinism(self):
        s=blank_site("A & B","<hello>");a=render_site(s,"home","public");b=render_site(s,"home","public");self.assertEqual(a,b);self.assertIn("&lt;hello&gt;",a);self.assertNotIn("<hello>",a)
    def test_no_script_or_handlers(self):
        html=render_site(blank_site("A"),"home","public").lower();self.assertNotIn("<script",html);self.assertNotIn("onclick=",html)
    def test_url_sanitization(self):self.assertEqual(safe_url("javascript:alert(1)"),"#");self.assertEqual(safe_url("http://bad.test"),"#")
    def test_https_url(self):self.assertEqual(safe_url("https://example.com/a"),"https://example.com/a")
if __name__=="__main__":unittest.main()
