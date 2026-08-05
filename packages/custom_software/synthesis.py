"""Open-ended capability and architecture synthesis.

Installed packs are optimization candidates. Domain identity is derived from the
request and never replaced with the nearest installed vertical.
"""
from __future__ import annotations

import re


PRIMITIVES={"authentication","authorization","tenant_storage","audit_log","file_storage","notifications","background_jobs","search","forms","tables"}

DOMAIN_FIXTURES={
 "football_competition_match_intelligence":{
  "signals":["football","league","squad","match intelligence","standings"],
  "roles":["owner","league_administrator","club_manager","coach","analyst","referee","player","viewer"],
  "entities":["league","season","club","squad","player","registration","fixture","match","match_event","formation","standing","player_statistic"],
  "capabilities":[("domain_rules","player_eligibility","player eligibility and registration validation","generate_engine"),("state_machine","live_match_engine","chronological live match events and score updates","generate_engine"),("algorithm","standings_engine","automatic standings and tie-break calculation","generate_engine"),("visualization","formation_editor","interactive tactical football pitch","generate_component"),("analytics","player_analytics","player statistics and performance analytics","generate_engine")],
  "pages":["competition_workspace","club_and_squad_workspace","formation_editor","live_match_console","standings","player_analytics"],
  "invariants":["a club cannot play itself","a goal updates the score exactly once","a player cannot be substituted into a match twice","a red-carded player cannot remain active","standings equal rule-derived match totals"],
  "stack":["vanilla-html-css","python-stdlib-http","sqlite-preview","python-stdlib-web"],
 },
 "stablecoin_fruit_marketplace":{
  "signals":["stablecoin","usdt","usdc","fruit marketplace"],
  "roles":["buyer","seller","fulfillment_operator","administrator"],
  "entities":["product","inventory_item","cart","order","stablecoin_invoice","payment_observation","fulfillment"],
  "capabilities":[("commerce","catalog_checkout","fruit catalog cart checkout and inventory","generate_engine"),("integration","stablecoin_adapter","sandbox and configured real USDT or USDC verification","integration_adapter"),("visualization","payment_qr","wallet address and QR invoice display","generate_component"),("state_machine","payment_lifecycle","payment detection confirmation and exception lifecycle","generate_engine")],
  "pages":["fruit_catalog","cart","stablecoin_checkout","order_status","inventory_operations"],
  "invariants":["private keys are never collected or persisted","wrong-network payments never confirm","expired invoices require verified evidence","inventory is not finalized before confirmation"],
  "stack":["nextjs-typescript","fastapi-python","postgresql","container-web"],
 },
 "immersive_3d_audio_universe":{
  "signals":["3d audio","three-dimensional audio","audio particle","audio universe","spectrogram","waveform"],
  "roles":["creator","collaborator","viewer","administrator"],
  "entities":["audio_project","audio_asset","track","visual_preset","analysis_snapshot","project_version"],
  "capabilities":[("media","audio_analysis","real-time waveform spectrum and spectrogram analysis","generate_engine"),("visualization","webgl_audio_scene","audio-driven WebGL geometry and particles","generate_component"),("media","multitrack_playback","multi-track upload playback and microphone input","generate_engine"),("reliability","canvas_fallback","two-dimensional fallback renderer","generate_component")],
  "pages":["project_library","audio_workspace","immersive_scene","preset_editor"],
  "invariants":["microphone use requires explicit permission","project versions preserve track references","fallback remains usable without WebGL"],
  "stack":["react-typescript-webgl","web-audio-api","postgresql-object-storage","container-web"],
 },
 "scientific_3d_event_explorer":{
  "signals":["scientific","event explorer","detector","tracks","vertices"],
  "roles":["scientist","reviewer","viewer","administrator"],
  "entities":["dataset","dataset_schema","scientific_event","track","hit","vertex","analysis_view","filter_preset"],
  "capabilities":[("file_processing","dataset_ingestion","streaming CSV and JSON validation","generate_engine"),("visualization","detector_renderer","3D tracks hits vertices and energy mapping","generate_component"),("analytics","distribution_engine","histograms scatter plots and particle distributions","generate_engine"),("search","event_filter_engine","large-dataset event filtering","generate_engine")],
  "pages":["dataset_import","validation_report","event_browser","detector_viewer","analytics_workspace"],
  "invariants":["invalid schemas produce actionable errors","missing optional measurements do not reject an event","large inputs are processed incrementally"],
  "stack":["react-typescript-webgl","fastapi-python-workers","postgresql-object-storage","container-worker-web"],
 },
 "emergency_response_field_command":{
  "signals":["emergency response","command center","incident","field responder"],
  "roles":["dispatcher","incident_commander","field_responder","analyst","administrator"],
  "entities":["incident","dispatch_assignment","responder_team","vehicle","location_update","incident_report","offline_update","status_event"],
  "capabilities":[("state_machine","incident_lifecycle","enforced incident and dispatch status transitions","generate_engine"),("mapping","command_map","incident vehicle and team map layers","generate_component"),("reliability","offline_sync","mobile offline update queue and conflict resolution","generate_engine"),("analytics","response_analytics","response time heatmap and utilization metrics","generate_engine")],
  "pages":["incident_intake","dispatch_board","command_map","mobile_responder","incident_report","operations_analytics"],
  "invariants":["only authorized command roles dispatch resources","offline events retain client ordering","status transitions append immutable audit events"],
  "stack":["react-typescript-pwa","fastapi-python-realtime","postgresql","container-web-worker"],
 },
}


def _slug(value:str)->str:
    return re.sub(r"[^a-z0-9]+","_",value.lower()).strip("_")[:64] or "custom_software"


def identify_domain(prompt:str)->tuple[str,dict|None,float]:
    text=prompt.lower();ranked=[]
    for key,spec in DOMAIN_FIXTURES.items():
        score=sum(2 if signal in text else 0 for signal in spec["signals"])
        ranked.append((score,key,spec))
    score,key,spec=max(ranked)
    if score:return key,spec,min(.99,.72+score*.04)
    words=[w for w in re.findall(r"[a-z][a-z0-9]+",text) if w not in {"build","create","make","application","platform","system","software","with","and","for","the","that"}]
    domain=_slug("_".join(words[:5]))
    return domain,None,.48


def synthesize(prompt:str)->dict:
    domain,spec,confidence=identify_domain(prompt)
    if spec is None:
        roles=["administrator","operator","viewer"]
        entities=[_slug(x) for x in re.findall(r"(?:manage|track|store|process)\s+([a-z][a-z ]{2,30})",prompt.lower())][:8] or ["domain_record"]
        capabilities=[("domain_logic","custom_domain_engine",f"implement domain behavior for {domain.replace('_',' ')}","generate_engine")]
        pages=["workspace","record_detail"]
        invariants=["tenant data remains isolated","state changes are audited"]
        stack=["react-typescript","fastapi-python","postgresql","container-web"]
    else:
        roles,entities,capabilities,pages,invariants,stack=(spec[k] for k in ("roles","entities","capabilities","pages","invariants","stack"))
    caps=[]
    for category,cid,description,implementation in capabilities:
        caps.append({"id":cid,"category":category,"description":description,"requirement":description,"implementation":implementation,"status":"planned"})
    for primitive in sorted(PRIMITIVES):
        caps.append({"id":primitive,"category":"platform","description":f"reuse managed {primitive.replace('_',' ')}","requirement":"platform reliability and security","implementation":"reuse_primitive","status":"planned"})
    nodes=[]
    for cap in caps:
        if cap["implementation"]!="reuse_primitive":
            kind={"generate_engine":"domain_engine","generate_component":"frontend_component","integration_adapter":"integration_adapter"}[cap["implementation"]]
            nodes.append({"id":cap["id"],"nodeType":kind,"name":cap["description"],"inputs":entities[:3],"outputs":[cap["id"]+"_result"],"invariants":invariants,"implementationRequired":True})
    return {"domain":domain,"confidence":confidence,"roles":roles,"entities":entities,"capabilities":caps,"pages":pages,"invariants":invariants,"stack":stack,"architectureNodes":nodes}


def choose_pack(synthesis:dict)->str|None:
    domain=synthesis["domain"]
    return domain if domain in {"field_service","quotation","inventory"} else None
