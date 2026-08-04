import io,zipfile
from .detector import detect_type,safe_filename,DetectionError
from .models import AttachmentInput,ParsedAttachment
from .parsers import parse_attachment
def process_archive(item,limits,depth=0):
    if depth>=limits.max_archive_depth:raise ValueError("nested archive depth exceeded")
    results=[];expanded=0
    with zipfile.ZipFile(io.BytesIO(item.content_bytes)) as archive:
        infos=archive.infolist()
        if len(infos)>limits.max_archive_files:raise ValueError("archive entry limit exceeded")
        for n,info in enumerate(infos):
            if info.is_dir():continue
            expanded+=info.file_size
            if expanded>limits.max_archive_expanded_bytes:raise ValueError("archive expanded-size limit exceeded")
            ratio=info.file_size/max(info.compress_size,1)
            if ratio>200:raise ValueError("archive compression ratio is unsafe")
            name=safe_filename(info.filename)
            if name!=info.filename.replace("\\","/").split("/")[-1]:
                # Archive paths are intentionally discarded; only the basename survives.
                pass
            data=archive.read(info)
            child=AttachmentInput(index=item.index*1000+n+1,filename=f"{item.filename}/{name}",declared_content_type=None,size_bytes=len(data),content_bytes=data)
            try:child.detected_content_type=detect_type(name,data);results.append(parse_attachment(child,limits.max_pdf_pages))
            except (DetectionError,ValueError,RuntimeError) as exc:
                results.append(ParsedAttachment(child.index,child.filename,"skipped","unsupported",warnings=[str(exc)]))
    return results
