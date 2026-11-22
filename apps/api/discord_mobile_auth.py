"""Opt-in Discord -> native Android handoff. No browser cookies are exported to the app.

The browser sees only OAuth state and a short-lived ticket. Redeeming the ticket
requires possession of the device-generated PKCE verifier and preauth CSRF proof.
"""
from __future__ import annotations
import base64
import hashlib
import hmac
from html import escape
import os
import re
import secrets
from datetime import datetime, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, HTMLResponse, Response
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.auth_cookies import PREAUTH_CSRF_COOKIE, set_session_cookies
from apps.api.dependencies import get_db
from apps.api.security import hash_token
from apps.api.session import _audit, _create_session, _rate_limit, auth_error
from packages.database.models import AppUser, AuthChallenge

router = APIRouter(prefix="/api/identities/discord/mobile", tags=["authentication"])
STATE_RE = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
CHALLENGE_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
TICKET_RE = re.compile(r"^[A-Za-z0-9_-]{40,120}$")
MOBILE_COOKIE = "operly_discord_mobile_state"
PURPOSE = "discord_mobile"
PUBLIC_CALLBACK = "https://dragonzpyder.xyz/api/identities/discord/callback"


def enabled() -> bool:
    from apps.api.discord_auth_router import _config
    client, secret, callback = _config()
    return (
        os.getenv("DISCORD_MOBILE_AUTH_ENABLED") == "1"
        and os.getenv("OPERLY_IDENTITY_OWNER") != "dragonzpyder"
        and bool(client and secret)
        # Browser cookies are scoped to DragonZpyder, so callbacks must return
        # to this exact first-party host; no transparent cross-host cookie relay.
        and callback == PUBLIC_CALLBACK
    )


def require_enabled() -> None:
    if not enabled():
        raise auth_error(503, "DISCORD_MOBILE_NOT_CONFIGURED",
                         "Discord mobile sign-in is not enabled for this deployment")


def is_mobile_callback(request:Request,state:str|None)->bool:
    saved=request.cookies.get(MOBILE_COOKIE,"")
    return bool(state and saved and hmac.compare_digest(state,saved))


def clear_mobile_cookie(response:Response)->None:
    response.delete_cookie(MOBILE_COOKIE,path="/")


def _challenge_hash(value:str)->str:
    return hash_token(value,purpose="discord-mobile")


def _pkce_hash(value:str)->str:
    return hash_token(value,purpose="discord-pkce")


def valid_start_values(state:str,code_challenge:str)->bool:
    return bool(STATE_RE.fullmatch(state) and CHALLENGE_RE.fullmatch(code_challenge))


@router.get("/start")
async def start(request:Request,state:str="",code_challenge:str="",db:AsyncSession=Depends(get_db)):
    require_enabled()
    if not valid_start_values(state,code_challenge):
        raise auth_error(400,"INVALID_MOBILE_OAUTH_REQUEST","Invalid device proof")
    await _rate_limit(db,"discord_mobile_start",request,combined_limit=15,ip_limit=15)
    from apps.api.discord_auth_router import _config, DISCORD_AUTHORIZE_URL
    client, _, callback=_config()
    challenge=AuthChallenge(
        purpose=PURPOSE, user_id=None, target_email="pending-mobile-auth@invalid.example",
        secret_hash=_challenge_hash(state),code_hash=_pkce_hash(code_challenge),
        expires_at=datetime.utcnow()+timedelta(minutes=10),
        consumed_at=None,attempt_count=0,max_attempts=1,delivery_status="pending",
    )
    db.add(challenge)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise auth_error(409,"MOBILE_OAUTH_ALREADY_STARTED","This sign-in attempt was already started")
    query=urlencode({"client_id":client,"response_type":"code",
                     "redirect_uri":callback,"scope":"identify email","state":state})
    response=RedirectResponse(f"{DISCORD_AUTHORIZE_URL}?{query}",status_code=302)
    response.set_cookie(MOBILE_COOKIE,state,max_age=600,httponly=True,secure=True,
                        samesite="lax",path="/")
    response.headers["Cache-Control"]="no-store"
    response.headers["Referrer-Policy"]="no-referrer"
    return response


async def lookup_callback(request:Request,db:AsyncSession,state:str|None)->AuthChallenge|None:
    if not is_mobile_callback(request,state):
        return None
    if not enabled():
        raise auth_error(503,"DISCORD_MOBILE_NOT_CONFIGURED","Mobile sign-in unavailable")
    challenge=await db.scalar(select(AuthChallenge).where(
        AuthChallenge.purpose==PURPOSE,AuthChallenge.secret_hash==_challenge_hash(state),
        AuthChallenge.consumed_at.is_(None),AuthChallenge.expires_at>datetime.utcnow(),
        AuthChallenge.delivery_status=="pending",
    ))
    if challenge is None:
        raise auth_error(403,"DISCORD_MOBILE_STATE_EXPIRED","Mobile login expired or already used")
    return challenge


async def complete_callback(db:AsyncSession,request:Request,challenge:AuthChallenge,
                            user:AppUser, state:str)->Response:
    ticket=secrets.token_urlsafe(40)
    original_hash=challenge.secret_hash
    outcome=await db.execute(update(AuthChallenge).where(
        AuthChallenge.id==challenge.id,AuthChallenge.secret_hash==original_hash,
        AuthChallenge.consumed_at.is_(None),AuthChallenge.delivery_status=="pending",
        AuthChallenge.expires_at>datetime.utcnow(),
    ).values(secret_hash=_challenge_hash(ticket),user_id=user.id,target_email=user.email,
             delivery_status="issued"))
    if outcome.rowcount!=1:
        await db.rollback()
        raise auth_error(409,"DISCORD_MOBILE_ALREADY_FINISHED","This sign-in has already been completed")
    _audit(db,"discord_mobile_callback","succeeded",request,user_id=user.id,metadata={"scope":"personal"})
    await db.commit()
    deep_link="dragonzpyder://auth/discord?"+urlencode({"state":state,"ticket":ticket})
    safe_link=escape(deep_link,quote=True)
    # Chrome blocks some automatic cross-app redirects after provider OAuth.
    # A user-activated link guarantees an explicit handoff without JavaScript
    # or copying browser cookies into native storage.
    page=("""<!doctype html><html lang="en"><head>
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Return to DragonZpyder</title>
    <style>body{background:#0e0f11;color:#f6f4f1;font:16px system-ui;margin:0;min-height:100vh;
    display:grid;place-items:center}main{padding:28px;max-width:420px;width:86%;text-align:center;
    border:1px solid #665047;border-radius:22px;background:#242124}
    strong{font-size:26px}p{color:#c2b8b0;line-height:1.5}
    a{display:block;background:#b94a21;color:white;padding:16px;border-radius:14px;
    margin-top:28px;text-decoration:none;font-weight:700}
    small{display:block;margin-top:18px;color:#ada39d}</style></head>
    <body><main><strong>DragonZpyder</strong><h1>Discord confirmed</h1>
    <p>Your Discord identity was verified. Return to the Android app to finish securing your session.</p>
    <a href="""+chr(34)+safe_link+chr(34)+""">Return to DragonZpyder</a>
    <small>This link expires after ten minutes and works only on your initiating device.</small>
    </main></body></html>""")
    response=HTMLResponse(page,status_code=200)
    clear_mobile_cookie(response)
    response.headers["Cache-Control"]="no-store"
    response.headers["Referrer-Policy"]="no-referrer"
    response.headers["Content-Security-Policy"]=(
        "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'none'; connect-src 'none'"
    )
    return response


@router.post("/redeem")
async def redeem(request:Request,db:AsyncSession=Depends(get_db)):
    require_enabled()
    # The mobile client gets a fresh preauth cookie from /api/auth/bootstrap;
    # the browser's cookie is not copied across application boundaries.
    header=request.headers.get("x-csrf-token","")
    cookie=request.cookies.get(PREAUTH_CSRF_COOKIE,"")
    if not header or not cookie or not hmac.compare_digest(cookie,header):
        raise auth_error(403,"MOBILE_CSRF_FAILED","Secure mobile session proof required")
    await _rate_limit(db,"discord_mobile_redeem",request,combined_limit=15,ip_limit=15)
    if request.headers.get("content-type","").split(";",1)[0].lower()!="application/json":
        raise auth_error(415,"JSON_REQUIRED","Send JSON")
    data=await request.json()
    if not isinstance(data,dict) or set(data)!={"ticket","verifier"}:
        raise auth_error(400,"INVALID_MOBILE_REDEMPTION","Missing device proof")
    ticket, verifier=data["ticket"],data["verifier"]
    if not isinstance(ticket,str) or not TICKET_RE.fullmatch(ticket) or not isinstance(verifier,str) or not STATE_RE.fullmatch(verifier):
        raise auth_error(400,"INVALID_MOBILE_REDEMPTION","Invalid device proof")
    expected=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    challenge=await db.scalar(select(AuthChallenge).where(
        AuthChallenge.secret_hash==_challenge_hash(ticket),
        AuthChallenge.purpose==PURPOSE,AuthChallenge.consumed_at.is_(None),
        AuthChallenge.expires_at>datetime.utcnow(),AuthChallenge.delivery_status=="issued"
    ))
    if challenge is None or not challenge.code_hash or not hmac.compare_digest(challenge.code_hash,_pkce_hash(expected)):
        raise auth_error(403,"MOBILE_OAUTH_REDEMPTION_FAILED","This mobile sign-in is expired or invalid")
    user=await db.get(AppUser,challenge.user_id) if challenge.user_id else None
    if user is None or not user.active:
        raise auth_error(403,"MOBILE_ACCOUNT_UNAVAILABLE","Account unavailable")
    claimed=await db.execute(update(AuthChallenge).where(
        AuthChallenge.id==challenge.id,AuthChallenge.consumed_at.is_(None),
        AuthChallenge.delivery_status=="issued"
    ).values(consumed_at=datetime.utcnow(),delivery_status="consumed"))
    if claimed.rowcount!=1:
        await db.rollback()
        raise auth_error(409,"MOBILE_OAUTH_REPLAY","Sign-in ticket already used")
    _,session,csrf=await _create_session(db,request,user.id,None)
    _audit(db,"discord_mobile_redeem","succeeded",request,user_id=user.id,
           metadata={"scope":"personal"})
    await db.commit()
    from fastapi.responses import JSONResponse
    response=JSONResponse({"ok":True,"scope":"personal"},headers={"Cache-Control":"no-store"})
    set_session_cookies(response,session,csrf)
    return response
