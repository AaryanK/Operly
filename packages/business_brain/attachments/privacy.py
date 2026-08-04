import re
from .models import Sensitivity
HIGH=[r"(?i)passport\s*(?:no|number|#)?\s*[:#-]?\s*[a-z0-9]{6,12}",r"(?i)(?:password|api[_ -]?key|secret|token)\s*[:=]\s*\S+",r"\b\d{3}-\d{2}-\d{4}\b"]
CONF=[r"(?i)date of birth|medical|diagnosis|bank account|routing number|credit card",r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"]
def infer_sensitivity(text:str)->Sensitivity:
    sample=text[:100_000]
    if any(re.search(p,sample) for p in HIGH):return "highly_sensitive"
    if any(re.search(p,sample,re.I) for p in CONF):return "confidential"
    return "internal" if sample.strip() else "public"
def redacted_name(filename):
    stem=re.sub(r"[^A-Za-z0-9._-]","_",filename)[:80]
    return stem or "attachment"
