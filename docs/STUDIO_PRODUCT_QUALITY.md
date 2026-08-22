# Operly Studio product-quality contract

This pass establishes the minimum experience expected before adding deeper coding-harness integration.

## Workspace
- Cosmic blue/violet/gold brand language across workspace pages.
- Section navigation is collapsible and persisted per browser.
- Controls are readable, keyboard-focusable, and visually consistent.

## Studio
- Canvas owns the majority of the usable editor width.
- Project and Inspector panes are independently collapsible.
- Preview fills the available canvas instead of behaving like a tiny nested browser.
- The bottom composer is a persistent command surface with contextual quick actions.
- Inspector actions adapt to whole-page, hero, CTA/button, or generic element selection.

## Website generation
- New sites pass through `packages.studio.design`: business context -> design plan -> section composition -> validated SiteSchema.
- Raw company context must not be dumped into marketing copy.
- SiteSchema supports layout variants for navigation, hero, grids, stats, CTA, footer, theme mode, visual style, container width, and typography family.
- Business-type inference selects different first-draft visual systems instead of one universal skeleton.
- The public renderer and CSS expose those variants as real layouts.
- Generated facts must remain grounded: no invented metrics, testimonials, awards, pricing, locations, or guarantees.

## Quality gate
A new site must render a complete editable first draft with navigation, hero, meaningful content structure, a conversion path, contact surface, and footer. CI covers design composition, variant rendering, Studio browser syntax, existing Solution/Studio tests, and the production-startup smoke test.
