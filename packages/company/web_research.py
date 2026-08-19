import asyncio
import ipaddress
import json
import os
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

MAX_BYTES=750_000;TIMEOUT=8.0;MAX_REDIRECTS=4

class UnsafeUrl(ValueError):pass

async def _addresses(host:str):
 loop=asyncio.get_running_loop();infos=await loop.getaddrinfo(host,None,type=socket.SOCK_STREAM)
 return {item[4][0] for item in infos}

def _public_address(value:str):
 ip=ipaddress.ip_address(value)
 return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified)

async def validate_public_url(url:str,resolver=_addresses):
 parsed=urlparse(url)
 if parsed.scheme not in {"http","https"} or not parsed.hostname or parsed.username or parsed.password:raise UnsafeUrl("Only public HTTP/HTTPS URLs are allowed")
 if parsed.hostname.lower() in {"localhost","localhost.localdomain","metadata.google.internal"}:raise UnsafeUrl("Local and metadata hosts are blocked")
 addresses=await resolver(parsed.hostname)
 if not addresses or any(not _public_address(x) for x in addresses):raise UnsafeUrl("URL resolves to a non-public address")
 return parsed

class Cleaner(HTMLParser):
 def __init__(self):super().__init__();self.skip=0;self.text=[];self.links=[];self.title=[];self.in_title=False
 def handle_starttag(self,tag,attrs):
  if tag in {"script","style","noscript","svg"}:self.skip+=1
  if tag=="title":self.in_title=True
  if tag=="a":
   href=dict(attrs).get("href")
   if href:self.links.append(href)
 def handle_endtag(self,tag):
  if tag in {"script","style","noscript","svg"} and self.skip:self.skip-=1
  if tag=="title":self.in_title=False
 def handle_data(self,data):
  if self.skip:return
  value=" ".join(data.split())
  if value:self.text.append(value)
  if self.in_title and value:self.title.append(value)

def _redact(text):
 text=re.sub(r"(?i)(api[_ -]?key|secret|token|password)\s*[:=]\s*\S+",r"\1=[redacted]",text)
 return text[:24_000]

async def fetch_page(url:str,*,client=None,resolver=_addresses,max_bytes=MAX_BYTES):
 own=client is None
 if own:client=httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT),follow_redirects=False,headers={"User-Agent":"OPERLY-CompanyResearch/1.0 (+https://operly.com)"})
 try:
  current=url
  for redirects in range(MAX_REDIRECTS+1):
   await validate_public_url(current,resolver)
   async with client.stream("GET",current,headers={"Accept":"text/html,application/xhtml+xml,text/plain"}) as response:
    if response.status_code in {301,302,303,307,308}:
     if redirects>=MAX_REDIRECTS:raise ValueError("Too many redirects")
     location=response.headers.get("location")
     if not location:raise ValueError("Redirect has no location")
     current=urljoin(current,location);continue
    response.raise_for_status();ctype=response.headers.get("content-type","").split(";",1)[0].lower()
    if ctype not in {"text/html","application/xhtml+xml","text/plain"}:raise ValueError("Unsupported content type")
    content=bytearray()
    async for chunk in response.aiter_bytes():
     content.extend(chunk)
     if len(content)>max_bytes:raise ValueError("Response exceeds content size limit")
    raw=content.decode(response.encoding or "utf-8",errors="replace")
    if ctype=="text/plain":return {"url":str(response.url),"title":"","text":_redact(raw),"links":[],"bytes":len(content),"untrusted":True}
    parser=Cleaner();parser.feed(raw)
    links=[]
    for href in parser.links[:300]:
     absolute=urljoin(str(response.url),href);p=urlparse(absolute)
     if p.scheme in {"http","https"}:links.append(absolute)
    return {"url":str(response.url),"title":" ".join(parser.title)[:300],"text":_redact("\n".join(parser.text)),"links":list(dict.fromkeys(links))[:100],"bytes":len(content),"untrusted":True}
  raise ValueError("Redirect limit exceeded")
 finally:
  if own:await client.aclose()

async def crawl_site(url:str,*,max_pages=5,max_depth=1,fetcher=fetch_page):
 max_pages=max(1,min(int(max_pages),10));max_depth=max(0,min(int(max_depth),2));origin=urlparse(url).hostname;queue=[(url,0)];seen=set();pages=[];failures=[]
 while queue and len(pages)<max_pages:
  current,depth=queue.pop(0)
  if current in seen:continue
  seen.add(current)
  try:page=await fetcher(current);pages.append(page)
  except Exception as error:failures.append({"url":current,"error":str(error)[:300]});continue
  if depth<max_depth:
   for link in page.get("links",[]):
    if urlparse(link).hostname==origin and link not in seen:queue.append((link,depth+1))
 return {"pages":pages,"failures":failures,"completion_reason":"page_limit" if queue and len(pages)>=max_pages else "crawl_complete"}

class SearchProvider:
 async def search(self,query,limit):raise NotImplementedError

class UnconfiguredSearchProvider(SearchProvider):
 async def search(self,query,limit):return {"configured":False,"results":[],"reason":"search_provider_unconfigured"}

class BraveSearchProvider(SearchProvider):
 def __init__(self,key):self.key=key
 async def search(self,query,limit):
  async with httpx.AsyncClient(timeout=TIMEOUT) as client:r=await client.get("https://api.search.brave.com/res/v1/web/search",params={"q":query,"count":min(limit,10)},headers={"Accept":"application/json","X-Subscription-Token":self.key});r.raise_for_status();data=r.json()
  return {"configured":True,"results":[{"title":x.get("title","")[:300],"url":x.get("url"),"description":x.get("description","")[:1000]} for x in data.get("web",{}).get("results",[])[:limit]]}

def search_provider():
 key=os.getenv("BRAVE_SEARCH_API_KEY","").strip();return BraveSearchProvider(key) if key else UnconfiguredSearchProvider()
