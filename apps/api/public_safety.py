"""Bound anonymous request bodies and apply a small per-process abuse backstop."""
import time
from collections import defaultdict,deque
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

class PublicEndpointSafetyMiddleware(BaseHTTPMiddleware):
    def __init__(self,app,max_body_bytes=1_048_576,requests_per_minute=30):super().__init__(app);self.max_body_bytes=max_body_bytes;self.limit=requests_per_minute;self.hits=defaultdict(deque)
    async def dispatch(self,request,call_next):
        if request.url.path.startswith("/api/public/"):
            try:length=int(request.headers.get("content-length","0"))
            except ValueError:length=self.max_body_bytes+1
            if length>self.max_body_bytes:return JSONResponse({"detail":"Request body is too large"},413)
            now=time.monotonic();key=(request.client.host if request.client else "unknown",request.url.path);bucket=self.hits[key]
            while bucket and bucket[0]<now-60:bucket.popleft()
            if len(bucket)>=self.limit:return JSONResponse({"detail":"Too many anonymous requests"},429)
            bucket.append(now)
        return await call_next(request)
