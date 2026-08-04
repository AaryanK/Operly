import csv,io,json,re,tempfile
from pathlib import Path
from .models import OutputFile,ParsedAttachment
def safe_cell(value):
    text=str(value or "")
    return "'"+text if text.startswith(("=","+","-","@")) else text
def safe_output_name(base,ext):
    stem=re.sub(r"[^A-Za-z0-9_-]+","-",base).strip("-")[:60] or "operly-result"
    return f"{stem}.{ext}"
def _rows(parsed,analyses):
    return [[str(p.index),p.filename,p.category,p.detected_type,p.sensitivity,analyses.get(p.index,""),"; ".join(p.warnings)] for p in parsed]
def generate_output(fmt,parsed,analyses,summary,temp_dir=None):
    if fmt=="message":return []
    folder=Path(temp_dir or tempfile.mkdtemp(prefix="operly-attachments-"));folder.mkdir(parents=True,exist_ok=True)
    headers=["attachment_index","filename","category","detected_type","sensitivity","analysis","warnings"];rows=_rows(parsed,analyses)
    ext="md" if fmt=="markdown" else ("txt" if fmt in {"txt","text"} else fmt);name=safe_output_name("operly-attachment-result",ext);path=folder/name
    if fmt=="json":path.write_text(json.dumps({"summary":summary,"attachments":[dict(zip(headers,row)) for row in rows]},ensure_ascii=False,indent=2),encoding="utf-8");mime="application/json"
    elif fmt=="csv":
        with path.open("w",encoding="utf-8-sig",newline="") as f:w=csv.writer(f);w.writerow(headers);w.writerows([[safe_cell(x) for x in row] for row in rows])
        mime="text/csv"
    elif fmt=="xlsx":
        try:from openpyxl import Workbook
        except ImportError as exc:raise RuntimeError("XLSX output dependency is unavailable") from exc
        wb=Workbook();ws=wb.active;ws.title="OPERLY Results";ws.append(headers)
        for row in rows:ws.append([safe_cell(x) for x in row])
        wb.save(path);mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif fmt=="docx":
        try:from docx import Document
        except ImportError as exc:raise RuntimeError("DOCX output dependency is unavailable") from exc
        doc=Document();doc.add_heading("OPERLY Attachment Report",0);doc.add_paragraph(summary)
        for p in parsed:doc.add_heading(f"{p.index}. {p.filename}",1);doc.add_paragraph(analyses.get(p.index,"No analysis available."))
        doc.save(path);mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif fmt=="pdf":
        try:
            from reportlab.lib.pagesizes import letter
            from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer
            from reportlab.lib.styles import getSampleStyleSheet
        except ImportError as exc:raise RuntimeError("PDF output dependency is unavailable") from exc
        styles=getSampleStyleSheet();story=[Paragraph("OPERLY Attachment Report",styles["Title"]),Paragraph(summary,styles["BodyText"])]
        for p in parsed:story += [Spacer(1,12),Paragraph(f"{p.index}. {p.filename}",styles["Heading2"]),Paragraph(analyses.get(p.index,"No analysis available.").replace("&","&amp;").replace("<","&lt;"),styles["BodyText"])]
        SimpleDocTemplate(str(path),pagesize=letter).build(story);mime="application/pdf"
    else:
        body="# OPERLY Attachment Report\n\n"+summary+"\n\n"+"\n\n".join(f"## {p.index}. {p.filename}\n\n{analyses.get(p.index,'')}" for p in parsed);path.write_text(body,encoding="utf-8");mime="text/markdown" if fmt=="markdown" else "text/plain"
    return [OutputFile(path,name,mime,path.stat().st_size)]
