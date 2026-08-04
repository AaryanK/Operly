from copy import deepcopy
from pydantic import BaseModel,ConfigDict,Field
from typing import Literal

ROLES={"owner","manager","employee"}
WIDTHS={"small","medium","large","full"}
VARIANTS={"default","highlight","subtle","warning"}
class ComponentDefinition(BaseModel):
    model_config=ConfigDict(extra="forbid")
    id:str;type:str;label:str;page_id:str;region:str="content"
    editable_properties:dict
    allowed_operations:list[str]
    data_source:str|None=None;action_binding:str|None=None
    visibility:list[str]=Field(default_factory=lambda:["owner","manager","employee"])
    children:list[str]=Field(default_factory=list)

def metric(id,label,order):return ComponentDefinition(id=id,type="MetricCard",label=label,page_id="overview",editable_properties={"title":label,"shown":True,"order":order,"width":"medium","variant":"default","visibility":["owner","manager","employee"]},allowed_operations=["rename","move","resize","change_variant","change_visibility","show","hide"])
REGISTRY={x.id:x for x in [
 metric("overview-messages-card","Messages captured",1),metric("overview-open-tasks-card","Open tasks",2),metric("overview-memories-card","Business facts",3),metric("overview-pending-approvals-card","Pending approvals",4),
 ComponentDefinition(id="overview-recent-conversations",type="DataPanel",label="Recent conversations",page_id="overview",editable_properties={"title":"Recent conversations","shown":True,"order":1,"width":"large","variant":"default","visibility":["owner","manager","employee"]},allowed_operations=["rename","move","resize","change_variant","change_visibility","show","hide"],data_source="messages"),
 ComponentDefinition(id="overview-control-loop",type="InfoPanel",label="OPERLY control loop",page_id="overview",editable_properties={"title":"OPERLY control loop","shown":True,"order":2,"width":"medium","variant":"subtle","visibility":["owner","manager"]},allowed_operations=["rename","move","resize","change_variant","change_visibility","show","hide"]),
]}
NAV_PAGES=["overview","induction","operationsCenter","audit","operatingPlan","assistant","studio","inbox","tasks","crm","catalog","sales","calendar","team","reports","memory","approvals","integrations","settings"]
for order,page in enumerate(NAV_PAGES,1):
    label={"operationsCenter":"Operations","operatingPlan":"Operating plan","assistant":"OPERLY AI"}.get(page,page.replace("_"," ").title())
    c=ComponentDefinition(id=f"nav-{page}",type="NavigationItem",label=label,page_id="global",region="sidebar",editable_properties={"label":label,"shown":True,"order":order,"visibility":["owner","manager","employee"],"action_binding":page},allowed_operations=["rename","move","change_visibility","show","hide"],action_binding=page)
    REGISTRY[c.id]=c
def get_component(component_id):return REGISTRY.get(component_id)
def screen_manifest(screen_id):return [deepcopy(x.model_dump()) for x in REGISTRY.values() if x.page_id in {screen_id,"global"}]
