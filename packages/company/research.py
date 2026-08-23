import hashlib
import json
import re
from datetime import datetime

from packages.company.events import append_event
from packages.company.intelligence import observe_evidence, synthesize_profile
from packages.company.web_research import crawl_site, search_provider
from packages.database.product_models import CompanyResearchRun


def _website(seed):
 match=re.search(r"https?://[^\s]+",seed,re.I)
 if match:return match.group(0).rstrip(".,)")
 if re.fullmatch(r"(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)+/?",seed.strip(),re.I):return "https://"+seed.strip().rstrip("/")


def _observations(page):
 text=page.get("text","");title=page.get("title","");out=[]
 if title:out.append(("display_name",re.split(r"[|–—-]",title)[0].strip(),.72))
 out.append(("website",page["url"],.95));emails=list(dict.fromkeys(re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",text)))[:5];phones=list(dict.fromkeys(re.findall(r"(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}",text)))[:5]
 if emails or phones:out.append(("contact",{"emails":emails,"phones":phones},.75))
 lower=text.lower()
 for kind in ("restaurant","plumber","contractor","consultant","saas","retailer","dropshipper"):
  if kind in lower:out.append(("business_type",kind,.62));break
 description=" ".join(text.split())[:500]
 if description:out.append(("description",description,.45))
 return out


def _research_subject(seed:str)->str:
 return "research:"+hashlib.sha256(" ".join(seed.lower().split()).encode()).hexdigest()[:24]


async def research_company(db,tenant_id,seed,*,max_searches=1,max_pages=5):
 """Research a target without making it the workspace's canonical identity.

 Owner initiation proves only that the user asked for research. It never means the
 returned facts were owner-confirmed as facts about the workspace itself.
 """
 run=CompanyResearchRun(tenant_id=tenant_id,seed=seed,status="running");db.add(run);await db.flush()
 subject_reference=_research_subject(seed);subject_name=" ".join(seed.split())[:200] or "Research target"
 await append_event(db,tenant_id=tenant_id,event_type="company.research.started",payload={"research_id":run.id,"page_budget":max_pages,"search_budget":max_searches,"subject_kind":"research_target","subject_reference":subject_reference},source="company_research")
 direct=_website(seed);search_state=None
 try:
  urls=[direct] if direct else []
  if not urls and max_searches:search_state=await search_provider().search(seed,min(5,max_pages));run.searches_used=1;urls=[x["url"] for x in search_state.get("results",[]) if x.get("url")]
  if not urls:
   # The raw seed is staged as low-confidence research evidence, never owner-confirmed.
   await observe_evidence(db,tenant_id,"description" if len(seed.split())>4 else "business_name",seed,"research",confidence=.35,owner_initiated=True,owner_confirmed=False,source_reference=f"research:{run.id}",research_run_id=run.id,subject_kind="research_target",subject_reference=subject_reference,subject_name=subject_name)
   reason=search_state.get("reason","no_public_url") if search_state else "no_public_url"
  else:
   result=await crawl_site(urls[0],max_pages=max_pages,max_depth=1);run.pages_used=len(result["pages"])
   for page in result["pages"]:
    for field,value,confidence in _observations(page):
     await observe_evidence(db,tenant_id,field,value,"website",source_url=page["url"],source_reference=f"research:{run.id}",confidence=confidence,owner_initiated=True,owner_confirmed=False,research_run_id=run.id,subject_kind="research_target",subject_reference=subject_reference,subject_name=subject_name)
   reason=result["completion_reason"]
  profile=await synthesize_profile(db,tenant_id,subject_kind="research_target",subject_reference=subject_reference,subject_name=subject_name)
  summary={"profile":profile,"found":sorted(profile["profile"]),"questions_remaining":0,"search":search_state,"canonical_workspace_changed":False,"requires_owner_confirmation_for_promotion":True}
  run.status="completed";run.completion_reason=reason;run.summary_json=json.dumps(summary,sort_keys=True);run.completed_at=datetime.utcnow();await append_event(db,tenant_id=tenant_id,event_type="company.research.completed",payload={"research_id":run.id,"completion_reason":reason,"pages_used":run.pages_used,"searches_used":run.searches_used,"found":summary["found"],"subject_reference":subject_reference,"canonical_workspace_changed":False},source="company_research");return {"research_id":run.id,"status":run.status,"completion_reason":reason,**summary}
 except Exception as error:
  run.status="failed";run.completion_reason="failed";run.summary_json=json.dumps({"error":str(error)[:500]});run.completed_at=datetime.utcnow();await append_event(db,tenant_id=tenant_id,event_type="company.research.completed",payload={"research_id":run.id,"completion_reason":"failed","subject_reference":subject_reference},source="company_research");return {"research_id":run.id,"status":"failed","completion_reason":"failed","error":str(error)[:500]}