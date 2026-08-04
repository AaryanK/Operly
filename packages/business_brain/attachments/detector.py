import re,zipfile,io
from pathlib import Path

EXECUTABLE_EXTENSIONS={".exe",".dll",".msi",".bat",".cmd",".ps1",".jar",".com",".scr",".vbs"}
MACRO_EXTENSIONS={".docm",".xlsm",".pptm",".xlam",".dotm"}
TEXT_EXTENSIONS={".txt",".md",".csv",".tsv",".json",".xml",".yaml",".yml",".html",".htm",".log",".css",".sql",".java",".c",".h",".cpp",".rs",".go",".rb",".php",".ts",".tsx",".jsx",".js",".py",".sh"}
SIGNATURES=[(b"%PDF-","application/pdf"),(b"\xff\xd8\xff","image/jpeg"),(b"\x89PNG\r\n\x1a\n","image/png"),(b"GIF87a","image/gif"),(b"GIF89a","image/gif")]
class DetectionError(ValueError):pass
def safe_filename(value):
    name=Path(value or "attachment").name
    return re.sub(r"[^A-Za-z0-9._ -]","_",name)[:180] or "attachment"
def _zip_type(data):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names=set(z.namelist())
            lowered={x.lower() for x in names}
            if any("vbaproject.bin" in x or "activex" in x or "embeddings/" in x for x in lowered):raise DetectionError("macro or embedded executable Office content is not supported")
            if "word/document.xml" in names:return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            if "ppt/presentation.xml" in names:return "application/vnd.openxmlformats-officedocument.presentationml.presentation"
            if "xl/workbook.xml" in names:return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if "mimetype" in names:
                mt=z.read("mimetype")[:100].decode("ascii","ignore")
                if mt in {"application/vnd.oasis.opendocument.text","application/vnd.oasis.opendocument.spreadsheet"}:return mt
            return "application/zip"
    except zipfile.BadZipFile:raise DetectionError("malformed ZIP or Office document")
def detect_type(filename,data,declared=None):
    ext=Path(filename).suffix.lower()
    if ext in EXECUTABLE_EXTENSIONS:raise DetectionError("executable or script files are not supported")
    if ext in MACRO_EXTENSIONS:raise DetectionError("macro-enabled Office files are not supported")
    if data.startswith(b"MZ"):raise DetectionError("Windows executable content is not supported")
    for sig,mime in SIGNATURES:
        if data.startswith(sig):return mime
    if data[:4]==b"RIFF" and data[8:12]==b"WEBP":return "image/webp"
    if data.startswith(b"PK\x03\x04"):return _zip_type(data)
    mapping={".docx":"application/vnd.openxmlformats-officedocument.wordprocessingml.document",".pptx":"application/vnd.openxmlformats-officedocument.presentationml.presentation",".xlsx":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",".odt":"application/vnd.oasis.opendocument.text",".ods":"application/vnd.oasis.opendocument.spreadsheet"}
    if ext in mapping:raise DetectionError("file signature does not match its Office extension")
    if ext in TEXT_EXTENSIONS:
        if b"\x00" in data[:4096]:raise DetectionError("binary content does not match text filename")
        return {".csv":"text/csv",".tsv":"text/tab-separated-values",".json":"application/json",".xml":"application/xml",".yaml":"application/yaml",".yml":"application/yaml",".html":"text/html"}.get(ext,"text/plain")
    raise DetectionError("unsupported or unknown file type")
