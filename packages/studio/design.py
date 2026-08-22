import re
from pydantic import BaseModel, ConfigDict

from packages.studio.schema import SiteSchema


class DesignPlan(BaseModel):
    model_config=ConfigDict(extra="forbid")
    visual_style:str
    theme_mode:str
    hero_variant:str
    grid_variant:str
    cta_variant:str
    primary:str
    accent:str
    audience:str
    conversion_goal:str


def _clean_copy(value:str, fallback:str)->str:
    raw=re.sub(r"\s+"," ",str(value or "")).strip()
    if not raw:return fallback
    parts=re.split(r"(?<=[.!?])\s+",raw)
    useful=[]
    for part in parts:
        low=part.lower()
        if any(token in low for token in ("facebook","instagram","http://","https://","www.","+977","call now")):
            continue
        useful.append(part.strip())
        if len(" ".join(useful))>=210:break
    copy=" ".join(useful).strip() or raw
    return copy[:260].rstrip(" ,;:-")


def infer_design_plan(name:str,description:str="",services:list[str]|None=None)->DesignPlan:
    haystack=" ".join([name,description,*[str(x) for x in (services or [])]]).lower()
    if any(x in haystack for x in ("travel","tour","trek","hotel","flight","adventure","destination")):
        return DesignPlan(visual_style="editorial",theme_mode="dark",hero_variant="immersive",grid_variant="media",cta_variant="split",primary="#4d91ff",accent="#ffb75e",audience="travelers looking for a confident local guide",conversion_goal="start a trip enquiry")
    if any(x in haystack for x in ("software","technology","ai ","saas","developer","platform","app")):
        return DesignPlan(visual_style="cosmic",theme_mode="dark",hero_variant="spotlight",grid_variant="bento",cta_variant="spotlight",primary="#6675ff",accent="#61d6ff",audience="modern digital customers",conversion_goal="start a conversation or product trial")
    if any(x in haystack for x in ("law","legal","finance","account","consult","advisory","insurance")):
        return DesignPlan(visual_style="luxury",theme_mode="dark",hero_variant="split",grid_variant="minimal",cta_variant="banner",primary="#344d7a",accent="#ffbe68",audience="clients seeking trust and expertise",conversion_goal="book a consultation")
    if any(x in haystack for x in ("beauty","salon","wellness","spa","health","therapy","fitness")):
        return DesignPlan(visual_style="editorial",theme_mode="light",hero_variant="editorial",grid_variant="cards",cta_variant="split",primary="#725c8f",accent="#e5a96b",audience="customers seeking a personal premium experience",conversion_goal="book an appointment")
    if any(x in haystack for x in ("restaurant","cafe","food","bakery","bar","kitchen")):
        return DesignPlan(visual_style="bold",theme_mode="dark",hero_variant="immersive",grid_variant="media",cta_variant="spotlight",primary="#b85d37",accent="#f0b55a",audience="local guests and returning customers",conversion_goal="visit, reserve, or order")
    return DesignPlan(visual_style="bold",theme_mode="dark",hero_variant="spotlight",grid_variant="cards",cta_variant="banner",primary="#5877ff",accent="#ffb75e",audience="prospective customers",conversion_goal="start a conversation")


def compose_initial_site(name:str,description:str="",services:list[str]|None=None,contact:dict|str|None=None)->SiteSchema:
    services=[str(x).strip() for x in (services or []) if str(x).strip()][:9]
    plan=infer_design_plan(name,description,services)
    summary=_clean_copy(description,f"A thoughtful, modern experience from {name}.")
    contact=contact or {}
    if isinstance(contact,dict):
        public_contact=", ".join([*[str(x) for x in contact.get("emails",[])],*[str(x) for x in contact.get("phones",[])]][:4])
    else:public_contact=str(contact)
    service_items=[{"title":item[:180],"description":f"Explore {item.lower()} with clear guidance and a straightforward experience."[:800]} for item in services]
    sections=[
        {"id":"navbar","type":"navbar","props":{"site_title":name,"navigation":[{"label":"Home","target":"home"}],"variant":"floating"}},
        {"id":"hero","type":"hero","props":{"eyebrow":"Designed around your next step","headline":name,"description":summary,"primary_button_text":"Get started","primary_button_target":"contact","secondary_button_text":"Explore what we offer","secondary_button_target":"services" if service_items else "why-us","alignment":"left","variant":plan.hero_variant}},
    ]
    if service_items:
        sections.append({"id":"services","type":"service_grid","props":{"heading":"What we can help with","description":"A focused set of services designed to make the next step simple.","items":service_items,"source_mode":"manual","catalog_item_ids":[],"variant":plan.grid_variant}})
    sections.append({"id":"why-us","type":"feature_grid","props":{"heading":f"Why people choose {name}","description":"Clear communication, thoughtful service, and an experience that respects your time.","variant":"bento" if plan.grid_variant=="bento" else "cards","items":[{"title":"Clear from the start","description":"Know what to expect, what comes next, and who to contact."},{"title":"Built around you","description":"The experience adapts to what you actually need instead of forcing a generic process."},{"title":"Easy to continue","description":"Move from interest to action without friction or unnecessary steps."}]}})
    sections.append({"id":"cta","type":"cta","props":{"heading":"Ready to take the next step?","description":f"Tell {name} what you need and get a clear path forward.","button_text":"Start a conversation","button_target":"contact","variant":plan.cta_variant}})
    sections.append({"id":"contact","type":"contact_form","props":{"heading":"Let’s talk","description":"Share what you’re looking for and we’ll take it from there.","form_key":"contact","submit_button_text":"Send enquiry","success_message":"Thanks — we’ll be in touch."}})
    sections.append({"id":"footer","type":"footer","props":{"business_name":name,"public_contact":public_contact,"navigation":[{"label":"Home","target":"home"}],"copyright_text":name,"variant":"columns"}})
    background="#070914" if plan.theme_mode=="dark" else "#f7f8fb"
    surface="#11182b" if plan.theme_mode=="dark" else "#ffffff"
    text="#f7f8ff" if plan.theme_mode=="dark" else "#12182a"
    muted="#a9b3c7" if plan.theme_mode=="dark" else "#657086"
    return SiteSchema.model_validate({
        "site":{"title":name,"description":summary,"seo":{"title":name,"description":summary[:320]}},
        "theme":{"primary":plan.primary,"accent":plan.accent,"background":background,"surface":surface,"text":text,"muted_text":muted,"border_radius":"large","font_family":"geometric","mode":plan.theme_mode,"visual_style":plan.visual_style,"container":"wide"},
        "pages":[{"id":"home","slug":"home","title":"Home","seo":{"title":name,"description":summary[:320]},"sections":sections}],
    })
