from dataclasses import dataclass,field
from pathlib import Path
from typing import Any,Literal

Sensitivity=Literal["public","internal","confidential","highly_sensitive"]
@dataclass(slots=True)
class AttachmentInput:
    index:int;filename:str;declared_content_type:str|None;size_bytes:int;content_bytes:bytes;detected_content_type:str="";rejection_reason:str=""
@dataclass(slots=True)
class ParsedAttachment:
    index:int;filename:str;category:str;detected_type:str;extracted_text:str="";tables:list[list[list[str]]]=field(default_factory=list);images:list[str]=field(default_factory=list);metadata:dict[str,Any]=field(default_factory=dict);warnings:list[str]=field(default_factory=list);sensitivity:Sensitivity="internal"
@dataclass(slots=True)
class AttachmentBundle:
    user_request:str;attachments:list[AttachmentInput];requested_output_format:str="message";tenant_id:str="";actor_id:str="";guild_id:int|None=None;channel_id:int|None=None;message_id:int|None=None
@dataclass(slots=True)
class OutputFile:
    path:Path;filename:str;content_type:str;size_bytes:int
@dataclass(slots=True)
class GeneratedOutput:
    message:str;files:list[OutputFile]=field(default_factory=list);warnings:list[str]=field(default_factory=list);operation_summary:str="";accepted:list[str]=field(default_factory=list);skipped:list[str]=field(default_factory=list)
