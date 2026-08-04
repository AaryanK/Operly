import csv,io,json
from .models import AttachmentInput,ParsedAttachment
from .image_processor import process_image
from .document_processor import parse_pdf,parse_docx,parse_pptx,parse_xlsx,parse_odf
from .privacy import infer_sensitivity
def decode_text(data):
    for encoding in ("utf-8-sig","utf-16","cp1252"):
        try:return data.decode(encoding)
        except UnicodeDecodeError:continue
    return data.decode("utf-8","replace")
def parse_text(item):
    text=decode_text(item.content_bytes)[:200_000];tables=[];warnings=[]
    if item.detected_content_type in {"text/csv","text/tab-separated-values"}:
        delimiter="\t" if item.detected_content_type.endswith("tab-separated-values") else ","
        try:tables=[[row[:100] for row in list(csv.reader(io.StringIO(text),delimiter=delimiter))[:5000]]]
        except csv.Error:warnings.append("CSV structure was malformed; treated as inert text")
    elif item.detected_content_type=="application/json":
        try:json.loads(text)
        except json.JSONDecodeError:warnings.append("JSON is malformed; treated as inert text")
    return ParsedAttachment(item.index,item.filename,"text",item.detected_content_type,text,tables=tables,warnings=warnings,sensitivity=infer_sensitivity(text))
def parse_attachment(item,max_pdf_pages=100):
    t=item.detected_content_type
    if t.startswith("image/"):return process_image(item)
    if t=="application/pdf":return parse_pdf(item,max_pdf_pages)
    if t.endswith("wordprocessingml.document"):return parse_docx(item)
    if t.endswith("presentationml.presentation"):return parse_pptx(item)
    if t.endswith("spreadsheetml.sheet"):return parse_xlsx(item)
    if t.startswith("application/vnd.oasis.opendocument"):return parse_odf(item)
    if t.startswith("text/") or t in {"application/json","application/xml","application/yaml"}:return parse_text(item)
    raise ValueError("unsupported parser type")
