import base64,io
from .models import AttachmentInput,ParsedAttachment
from .privacy import infer_sensitivity
def process_image(item:AttachmentInput)->ParsedAttachment:
    warnings=[];metadata={};model_bytes=item.content_bytes
    try:
        from PIL import Image
        with Image.open(io.BytesIO(item.content_bytes)) as image:
            metadata={"width":image.width,"height":image.height,"format":image.format}
            if image.width*image.height>36_000_000:raise ValueError("image dimensions exceed safe limit")
            image.load()
            if image.format=="GIF":
                converted=io.BytesIO();image.seek(0);image.convert("RGB").save(converted,"PNG");model_bytes=converted.getvalue();warnings.append("animated GIF analyzed using its first frame")
    except ImportError: warnings.append("Pillow unavailable; image signature was validated but pixels were not decoded")
    except Exception as exc: raise ValueError(f"unreadable image: {exc}") from exc
    return ParsedAttachment(index=item.index,filename=item.filename,category="image",detected_type=item.detected_content_type,images=[base64.b64encode(model_bytes).decode("ascii")],metadata=metadata,warnings=warnings,sensitivity="internal")
