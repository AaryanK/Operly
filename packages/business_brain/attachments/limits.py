import os
from dataclasses import dataclass
def env_int(name,default,minimum=1,maximum=10000):
    try:return max(minimum,min(maximum,int(os.getenv(name,str(default)))))
    except ValueError:return default
@dataclass(frozen=True,slots=True)
class AttachmentLimits:
    max_attachments:int=env_int("OPERLY_MAX_ATTACHMENTS",10,1,25)
    max_attachment_bytes:int=env_int("OPERLY_MAX_ATTACHMENT_MB",10,1,100)*1024*1024
    max_total_bytes:int=env_int("OPERLY_MAX_TOTAL_ATTACHMENT_MB",50,1,250)*1024*1024
    max_pdf_pages:int=env_int("OPERLY_MAX_PDF_PAGES",100,1,300)
    max_archive_files:int=env_int("OPERLY_MAX_ARCHIVE_FILES",50,1,200)
    max_archive_expanded_bytes:int=env_int("OPERLY_MAX_ARCHIVE_EXPANDED_MB",100,1,500)*1024*1024
    timeout_seconds:int=env_int("OPERLY_ATTACHMENT_TIMEOUT_SECONDS",300,10,900)
    max_text_chars:int=200_000;max_prompt_chars:int=80_000;max_output_chars:int=12_000;max_archive_depth:int=1
