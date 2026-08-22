import json, re
from typing import Annotated, Literal, Union
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator, field_validator

class Strict(BaseModel): model_config=ConfigDict(extra="forbid",str_strip_whitespace=True)
Align=Literal["left","center","right"]
class SEO(Strict): title:str=Field(max_length=120); description:str=Field(max_length=320)
class NavItem(Strict): label:str=Field(max_length=80); target:str=Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
class NavbarProps(Strict):
    site_title:str=Field(max_length=120); navigation:list[NavItem]=Field(default_factory=list,max_length=20); logo_asset_id:str|None=None
    variant:Literal["minimal","floating","solid"]="floating"
class HeroProps(Strict):
    eyebrow:str=""; headline:str=Field(max_length=240); description:str=Field(default="",max_length=1200); primary_button_text:str="Contact us"; primary_button_target:str="contact"; secondary_button_text:str=""; secondary_button_target:str=""; image_asset_id:str|None=None; alignment:Align="left"
    variant:Literal["split","centered","spotlight","editorial","immersive"]="split"
class TextProps(Strict): heading:str=Field(max_length=240); body:str=Field(max_length=6000); alignment:Align="left"
class ImageProps(Strict): asset_id:str|None=None; url:HttpUrl|None=None; alt_text:str=Field(max_length=300); caption:str=Field(default="",max_length=500); aspect_ratio:Literal["square","landscape","portrait","wide"]="landscape"
class Item(Strict): title:str=Field(max_length=180); description:str=Field(max_length=800); image_asset_id:str|None=None
class GridProps(Strict):
    heading:str=Field(max_length=240); description:str=Field(default="",max_length=1200); items:list[Item]=Field(default_factory=list,max_length=24)
    variant:Literal["cards","bento","minimal","media","steps"]="cards"
class ProductGridProps(GridProps): source_mode:Literal["manual","operly_catalog"]="manual"; catalog_item_ids:list[str]=Field(default_factory=list,max_length=24)
class Stat(Strict): value:str=Field(max_length=40); label:str=Field(max_length=100)
class StatsProps(Strict): heading:str=""; items:list[Stat]=Field(max_length=12); variant:Literal["strip","cards"]="strip"
class TestimonialProps(Strict): quote:str=Field(max_length=1500); author:str=Field(max_length=150); role:str=""
class FAQItem(Strict): question:str=Field(max_length=300); answer:str=Field(max_length=1500)
class FAQProps(Strict): heading:str=Field(max_length=240); items:list[FAQItem]=Field(max_length=20)
class CTAProps(Strict):
    heading:str=Field(max_length=240); description:str=Field(default="",max_length=1200); button_text:str=Field(max_length=80); button_target:str=Field(max_length=80)
    variant:Literal["banner","split","spotlight"]="banner"
class FormProps(Strict): heading:str=Field(max_length=240); description:str=""; form_key:str=Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,79}$"); submit_button_text:str="Submit"; success_message:str="Thank you."
class FooterProps(Strict):
    business_name:str=Field(max_length=200); public_contact:str=""; navigation:list[NavItem]=Field(default_factory=list,max_length=20); copyright_text:str=""
    variant:Literal["compact","columns"]="columns"

def section(name, props):
    return type(name,(Strict,),{"__annotations__":{"id":str,"type":Literal[name.removesuffix('Section').lower()],"enabled":bool,"props":props},"enabled":True})
class NavbarSection(Strict): id:str; type:Literal["navbar"]; enabled:bool=True; props:NavbarProps
class HeroSection(Strict): id:str; type:Literal["hero"]; enabled:bool=True; props:HeroProps
class TextSection(Strict): id:str; type:Literal["text"]; enabled:bool=True; props:TextProps
class ImageSection(Strict): id:str; type:Literal["image"]; enabled:bool=True; props:ImageProps
class StatsSection(Strict): id:str; type:Literal["stats"]; enabled:bool=True; props:StatsProps
class FeatureSection(Strict): id:str; type:Literal["feature_grid"]; enabled:bool=True; props:GridProps
class ProductSection(Strict): id:str; type:Literal["product_grid"]; enabled:bool=True; props:ProductGridProps
class ServiceSection(Strict): id:str; type:Literal["service_grid"]; enabled:bool=True; props:ProductGridProps
class GallerySection(Strict): id:str; type:Literal["gallery"]; enabled:bool=True; props:GridProps
class TestimonialSection(Strict): id:str; type:Literal["testimonial"]; enabled:bool=True; props:TestimonialProps
class FAQSection(Strict): id:str; type:Literal["faq"]; enabled:bool=True; props:FAQProps
class CTASection(Strict): id:str; type:Literal["cta"]; enabled:bool=True; props:CTAProps
class ContactSection(Strict): id:str; type:Literal["contact_form"]; enabled:bool=True; props:FormProps
class FooterSection(Strict): id:str; type:Literal["footer"]; enabled:bool=True; props:FooterProps
Section=Annotated[Union[NavbarSection,HeroSection,TextSection,ImageSection,StatsSection,FeatureSection,ProductSection,ServiceSection,GallerySection,TestimonialSection,FAQSection,CTASection,ContactSection,FooterSection],Field(discriminator="type")]
class Theme(Strict):
    primary:str="#185d43"; accent:str="#b9ee72"; background:str="#ffffff"; surface:str="#f3f5f1"; text:str="#13231c"; muted_text:str="#6e7b74"
    border_radius:Literal["none","small","medium","large"]="medium"
    font_family:Literal["system","serif","geometric"]="system"
    mode:Literal["light","dark"]="light"
    visual_style:Literal["minimal","editorial","luxury","playful","cosmic","bold"]="minimal"
    container:Literal["compact","standard","wide"]="standard"
    @field_validator("primary","accent","background","surface","text","muted_text")
    @classmethod
    def color(cls,v):
        if not re.fullmatch(r"#[0-9a-fA-F]{6}",v): raise ValueError("invalid hex color")
        return v.lower()
class SiteInfo(Strict): title:str=Field(max_length=150); description:str=Field(default="",max_length=500); language:str=Field(default="en",pattern=r"^[a-z]{2}(-[A-Z]{2})?$"); seo:SEO
class Page(Strict): id:str=Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$"); slug:str=Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$"); title:str=Field(max_length=150); seo:SEO; sections:list[Section]=Field(default_factory=list,max_length=50)
class SiteSchema(Strict):
    schema_version:Literal[1]=1; site:SiteInfo; theme:Theme; pages:list[Page]=Field(min_length=1,max_length=20)
    @model_validator(mode="after")
    def unique_safe(self):
        slugs=[p.slug for p in self.pages]; ids=[s.id for p in self.pages for s in p.sections]
        if len(slugs)!=len(set(slugs)): raise ValueError("duplicate page slug")
        if len(ids)!=len(set(ids)): raise ValueError("duplicate section id")
        raw=json.dumps(self.model_dump(mode="json"))
        if len(raw)>500_000: raise ValueError("site document too large")
        if re.search(r"(?i)<\s*(script|style|iframe|object|embed)|javascript:|vbscript:|on\w+\s*=",raw): raise ValueError("dangerous content")
        return self

def blank_site(title:str, description:str="") -> SiteSchema:
    from packages.studio.design import compose_initial_site
    return compose_initial_site(title,description)
