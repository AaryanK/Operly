from html import escape
from urllib.parse import urlparse
from packages.studio.schema import SiteSchema

def e(value): return escape(str(value or ""), quote=True)
def safe_url(value, fallback="#"):
    value=str(value or "")
    if value.startswith(("/sites/","/public-assets/","#")): return e(value)
    parsed=urlparse(value)
    return e(value) if parsed.scheme=="https" and parsed.netloc else fallback

def render_site(schema: SiteSchema, page_slug:str, public_slug:str, forms:dict|None=None, assets:dict|None=None, catalog:list|None=None)->str:
    page=next((p for p in schema.pages if p.slug==page_slug),None)
    if not page: raise KeyError("Page not found")
    assets=assets or {}; forms=forms or {}; out=[]
    for s in page.sections:
        if not s.enabled: continue
        p=s.props; sid=e(s.id)
        if s.type=="navbar": out.append(f'<nav id="{sid}"><strong>{e(p.site_title)}</strong><div>'+"".join(f'<a href="/sites/{e(public_slug)}/{e(n.target)}">{e(n.label)}</a>' for n in p.navigation)+"</div></nav>")
        elif s.type=="hero": out.append(f'<section id="{sid}" class="hero align-{p.alignment}"><small>{e(p.eyebrow)}</small><h1>{e(p.headline)}</h1><p>{e(p.description)}</p><a class="button" href="#{e(p.primary_button_target)}">{e(p.primary_button_text)}</a></section>')
        elif s.type=="text": out.append(f'<section id="{sid}" class="align-{p.alignment}"><h2>{e(p.heading)}</h2><p>{e(p.body)}</p></section>')
        elif s.type=="image":
            url=assets.get(p.asset_id) if p.asset_id else p.url
            out.append(f'<figure id="{sid}"><img src="{safe_url(url)}" alt="{e(p.alt_text)}" loading="lazy"><figcaption>{e(p.caption)}</figcaption></figure>')
        elif s.type=="stats": out.append(f'<section id="{sid}"><h2>{e(p.heading)}</h2><div class="grid">'+"".join(f'<article><b>{e(x.value)}</b><p>{e(x.label)}</p></article>' for x in p.items)+"</div></section>")
        elif s.type in {"feature_grid","product_grid","service_grid","gallery"}:
            items=p.items
            out.append(f'<section id="{sid}"><h2>{e(p.heading)}</h2><p>{e(p.description)}</p><div class="grid">'+"".join(f'<article><h3>{e(x.title)}</h3><p>{e(x.description)}</p></article>' for x in items)+"</div></section>")
        elif s.type=="testimonial": out.append(f'<blockquote id="{sid}"><p>“{e(p.quote)}”</p><cite>{e(p.author)} — {e(p.role)}</cite></blockquote>')
        elif s.type=="faq": out.append(f'<section id="{sid}"><h2>{e(p.heading)}</h2>'+"".join(f'<details><summary>{e(x.question)}</summary><p>{e(x.answer)}</p></details>' for x in p.items)+"</section>")
        elif s.type=="cta": out.append(f'<section id="{sid}" class="cta"><h2>{e(p.heading)}</h2><p>{e(p.description)}</p><a class="button" href="#{e(p.button_target)}">{e(p.button_text)}</a></section>')
        elif s.type=="contact_form":
            fields=forms.get(p.form_key,[{"key":"name","label":"Full name","type":"text","required":True},{"key":"email","label":"Email","type":"email","required":True},{"key":"message","label":"Message","type":"textarea","required":True}])
            inputs="".join(f'<label>{e(x["label"])}'+(f'<textarea name="{e(x["key"])}" {"required" if x.get("required") else ""}></textarea>' if x.get("type")=="textarea" else f'<input name="{e(x["key"])}" type="{e(x.get("type","text"))}" {"required" if x.get("required") else ""}>')+'</label>' for x in fields)
            out.append(f'<section id="{sid}"><h2>{e(p.heading)}</h2><p>{e(p.description)}</p><form data-form-key="{e(p.form_key)}" method="post" action="/api/public/sites/{e(public_slug)}/forms/{e(p.form_key)}"><input class="hp" name="website" tabindex="-1" autocomplete="off">{inputs}<input type="hidden" name="page_slug" value="{e(page.slug)}"><button>{e(p.submit_button_text)}</button></form></section>')
        elif s.type=="footer": out.append(f'<footer id="{sid}"><strong>{e(p.business_name)}</strong><p>{e(p.public_contact)}</p><small>{e(p.copyright_text)}</small></footer>')
    t=schema.theme; radius={"none":"0","small":"6px","medium":"14px","large":"28px"}[t.border_radius]
    return f'''<!doctype html><html lang="{e(schema.site.language)}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(page.seo.title)}</title><meta name="description" content="{e(page.seo.description)}"><link rel="stylesheet" href="/static/studio-public.css"><style>:root{{--primary:{t.primary};--accent:{t.accent};--background:{t.background};--surface:{t.surface};--text:{t.text};--muted:{t.muted_text};--radius:{radius}}}</style></head><body><main>{''.join(out)}</main></body></html>'''
