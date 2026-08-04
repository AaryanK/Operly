from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator

class Strict(BaseModel):model_config=ConfigDict(extra="forbid")
class SelectedComponent(Strict):
    id:str=Field(max_length=120);type:str=Field(max_length=80);label:str=Field(max_length=200);editable_properties:list[str]=Field(max_length=20)
class ContextEnvelope(Strict):
    workspace_id:str=Field(max_length=36);route:str=Field(max_length=200);screen_id:str=Field(max_length=100);screen_title:str=Field(max_length=150);mode:Literal["operate","customize"]="operate";selected_components:list[SelectedComponent]=Field(default_factory=list,max_length=20);selected_records:list[str]=Field(default_factory=list,max_length=50);user_role:str=Field(max_length=30);active_app_version:str|None=None;viewport:Literal["desktop","tablet","mobile"]="desktop"
class OperationInput(Strict):
    operation:Literal["update_component","move_component","change_visibility"]
    component_id:str=Field(max_length=120);changes:dict
class ProposalInput(Strict):
    message:str=Field(min_length=1,max_length=4000);conversation_id:str|None=Field(default=None,max_length=120);context:ContextEnvelope
class ChangeSetInput(Strict):
    screen_id:str=Field(max_length=100);originating_chat_message:str=Field(max_length=4000);explanation:str=Field(max_length=1000);operations:list[OperationInput]=Field(min_length=1,max_length=50)
