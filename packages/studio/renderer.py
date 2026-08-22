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
        p=s.props; sid=e(s.id); section_class=f"section-{e(s.type)}"
        if s.type=="navbar":
            nav="".join(f'<a href="/sites/{e(public_slug)}/{e(n.target)}">{e(n.label)}</a>' for n in p.navigation)
            out.append(f'<nav id="{sid}" class="{section_class} variant-{e(p.variant)}" data-operly-section-type="navbar"><a class="brand" href="/sites/{e(public_slug)}/home"><span class="brand-mark"></span><strong>{e(p.site_title)}</strong></a><div class="nav-links">{nav}</div><a class="nav-cta" href="#contact">Get in touch</a></nav>')
        elif s.type=="hero":
            image=assets.get(p.image_asset_id) if p.image_asset_id else None
            visual=f'<img src="{safe_url(image)}" alt="" loading="eager">' if image else '<div class="hero-orbit"><i></i><i></i><i></i></div>'
            secondary=f'<a class="button secondary" href="#{e(p.secondary_button_target)}">{e(p.secondary_button_text)}</a>' if p.secondary_button_text and p.secondary_button_target else ""
            out.append(f'<section id="{sid}" class="{section_class} hero align-{e(p.alignment)} variant-{e(p.variant)}" data-operly-section-type="hero"><div class="hero-copy"><small>{e(p.eyebrow)}</small><h1>{e(p.headline)}</h1><p>{e(p.description)}</p><div class="hero-actions"><a class="button" href="#{e(p.primary_button_target)}">{e(p.primary_button_text)}</a>{secondary}</div></div><div class="hero-visual">{visual}</div></section>')
        elif s.type=="text": out.append(f'<section id="{sid}" class="{section_class} align-{e(p.alignment)}" data-operly-section-type="text"><div class="section-heading"><h2>{e(p.heading)}</h2></div><p class="prose">{e(p.body)}</p></section>')
        elif s.type=="image":
            url=assets.get(p.asset_id) if p.asset_id else p.url
            out.append(f'<figure id="{sid}" class="{section_class} ratio-{e(p.aspect_ratio)}" data-operly-section-type="image"><img src="{safe_url(url)}" alt="{e(p.alt_text)}" loading="lazy"><figcaption>{e(p.caption)}</figcaption></figure>')
        elif s.type=="stats": out.append(f'<section id="{sid}" class="{section_class} variant-{e(p.variant)}" data-operly-section-type="stats"><div class="section-heading"><h2>{e(p.heading)}</h2></div><div class="stat-grid">'+"".join(f'<article><b>{e(x.value)}</b><p>{e(x.label)}</p></article>' for x in p.items)+"</div></section>")
        elif s.type in {"feature_grid","product_grid","service_grid","gallery"}:
            cards=[]
            for x in p.items:
                image=assets.get(x.image_asset_id) if x.image_asset_id else None
                media=f'<img src="{safe_url(image)}" alt="" loading="lazy">' if image else '<div class="card-visual"><span></span></div>'
                cards.append(f'<article>{media}<div class="card-copy"><h3>{e(x.title)}</h3><p>{e(x.description)}</p></div></article>')
            out.append(f'<section id="{sid}" class="{section_class} variant-{e(p.variant)}" data-operly-section-type="{e(s.type)}"><div class="section-heading"><small>{"Explore" if s.type in {"product_grid","service_grid","gallery"} else "Why it works"}</small><h2>{e(p.heading)}</h2><p>{e(p.description)}</p></div><div class="content-grid">'+"".join(cards)+"</div></section>")
        elif s.type=="testimonial": out.append(f'<blockquote id="{sid}" class="{section_class}" data-operly-section-type="testimonial"><span class="quote-mark">“</span><p>{e(p.quote)}</p><cite>{e(p.author)}<small>{e(p.role)}</small></cite></blockquote>')
        elif s.type=="faq": out.append(f'<section id="{sid}" class="{section_class}" data-operly-section-type="faq"><div class="section-heading"><small>Good to know</small><h2>{e(p.heading)}</h2></div><div class="faq-list">'+"".join(f'<details><summary>{e(x.question)}</summary><p>{e(x.answer)}</p></details>' for x in p.items)+"</div></section>")
        elif s.type=="cta": out.append(f'<section id="{sid}" class="{section_class} cta variant-{e(p.variant)}" data-operly-section-type="cta"><div><small>Next step</small><h2>{e(p.heading)}</h2><p>{e(p.description)}</p></div><a class="button" href="#{e(p.button_target)}">{e(p.button_text)}</a></section>')
        elif s.type=="contact_form":
            fields=forms.get(p.form_key,[{"key":"name","label":"Full name","type":"text","required":True},{"key":"email","label":"Email","type":"email","required":True},{"key":"message","label":"Message","type":"textarea","required":True}])
            inputs="".join(f'<label>{e(x["label"])}'+(f'<textarea name="{e(x["key"])}" {"required" if x.get("required") else ""}></textarea>' if x.get("type")=="textarea" else f'<input name="{e(x["key"])}" type="{e(x.get("type","text"))}" {"required" if x.get("required") else ""}>')+'</label>' for x in fields)
            out.append(f'<section id="{sid}" class="{section_class} contact" data-operly-section-type="contact_form"><div class="section-heading"><small>Contact</small><h2>{e(p.heading)}</h2><p>{e(p.description)}</p></div><form data-form-key="{e(p.form_key)}" method="post" action="/api/public/sites/{e(public_slug)}/forms/{e(p.form_key)}"><input class="hp" name="website" tabindex="-1" autocomplete="off">{inputs}<input type="hidden" name="page_slug" value="{e(page.slug)}"><button>{e(p.submit_button_text)}</button></form></section>')
        elif s.type=="footer":
            nav="".join(f'<a href="/sites/{e(public_slug)}/{e(n.target)}">{e(n.label)}</a>' for n in p.navigation)
            out.append(f'<footer id="{sid}" class="{section_class} variant-{e(p.variant)}" data-operly-section-type="footer"><div class="footer-brand"><span class="brand-mark"></span><strong>{e(p.business_name)}</strong><p>{e(p.public_contact)}</p></div><div class="footer-links">{nav}</div><small>{e(p.copyright_text)}</small></footer>')
    t=schema.theme; radius={"none":"0","small":"8px","medium":"16px","large":"28px"}[t.border_radius];container={"compact":"960px","standard":"1180px","wide":"1360px"}[t.container]
    body_class=f"theme-{e(t.mode)} style-{e(t.visual_style)} font-{e(t.font_family)}"
    return f'''<!doctype html><html lang="{e(schema.site.language)}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(page.seo.title)}</title><meta name="description" content="{e(page.seo.description)}"><link rel="stylesheet" href="/static/studio-public.css?v=20260822-design-v2"><style>:root{{--primary:{t.primary};--accent:{t.accent};--background:{t.background};--surface:{t.surface};--text:{t.text};--muted:{t.muted_text};--radius:{radius};--container:{container}}}</style></head><body class="{body_class}"><main>{''.join(out)}</main></body></html>'''