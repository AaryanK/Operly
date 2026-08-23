"""Owner-facing scoped company-evidence provenance and conflict resolution."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.company.events import append_event
from packages.company.intelligence import profile_payload, synthesize_profile
from packages.database.scope_models import (
    ProfileSubject,
    ScopedCompanyEvidence,
    ScopedCompanyProfile,
)


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return fallback


def subject_json(subject: ProfileSubject, profile: ScopedCompanyProfile | None = None) -> dict[str, Any]:
    payload = profile_payload(profile)
    return {
        "id": subject.id,
        "kind": subject.kind,
        "reference_id": subject.reference_id,
        "display_name": subject.display_name,
        "inherits_workspace": subject.inherits_workspace,
        "created_at": subject.created_at.isoformat(),
        "updated_at": subject.updated_at.isoformat(),
        "profile": payload["profile"],
        "fields": payload["fields"],
        "conflicts": payload["conflicts"],
    }


def evidence_json(row: ScopedCompanyEvidence) -> dict[str, Any]:
    return {
        "id": row.id,
        "subject_id": row.subject_id,
        "field": row.field_key,
        "value": _loads(row.value_json, None),
        "source_type": row.source_type,
        "source_url": row.source_url,
        "source_reference": row.source_reference,
        "actor_user_id": row.actor_user_id,
        "conversation_id": row.conversation_id,
        "action_id": row.action_id,
        "research_run_id": row.research_run_id,
        "confidence": row.confidence,
        "owner_initiated": row.owner_initiated,
        "owner_confirmed": row.owner_confirmed,
        "superseded": row.superseded,
        "stale": row.stale,
        "content_hash": row.content_hash,
        "observed_at": row.observed_at.isoformat(),
    }


async def _subject(db: AsyncSession, tenant_id: str, subject_id: str) -> ProfileSubject:
    row = await db.scalar(
        select(ProfileSubject).where(
            ProfileSubject.id == subject_id,
            ProfileSubject.tenant_id == tenant_id,
        )
    )
    if row is None:
        raise LookupError("Profile subject not found")
    return row


async def list_subjects(db: AsyncSession, tenant_id: str) -> list[dict[str, Any]]:
    subjects = list(
        (
            await db.scalars(
                select(ProfileSubject)
                .where(ProfileSubject.tenant_id == tenant_id)
                .order_by(ProfileSubject.kind, ProfileSubject.created_at)
            )
        ).all()
    )
    profiles = list(
        (
            await db.scalars(
                select(ScopedCompanyProfile).where(ScopedCompanyProfile.tenant_id == tenant_id)
            )
        ).all()
    )
    by_subject = {row.subject_id: row for row in profiles}
    return [subject_json(subject, by_subject.get(subject.id)) for subject in subjects]


async def get_subject(db: AsyncSession, tenant_id: str, subject_id: str) -> dict[str, Any]:
    subject = await _subject(db, tenant_id, subject_id)
    profile = await db.scalar(
        select(ScopedCompanyProfile).where(
            ScopedCompanyProfile.tenant_id == tenant_id,
            ScopedCompanyProfile.subject_id == subject.id,
        )
    )
    payload = subject_json(subject, profile)
    rows = list(
        (
            await db.scalars(
                select(ScopedCompanyEvidence)
                .where(
                    ScopedCompanyEvidence.tenant_id == tenant_id,
                    ScopedCompanyEvidence.subject_id == subject.id,
                    ScopedCompanyEvidence.superseded.is_(False),
                    ScopedCompanyEvidence.stale.is_(False),
                )
                .order_by(ScopedCompanyEvidence.field_key, ScopedCompanyEvidence.observed_at.desc())
            )
        ).all()
    )
    payload["active_evidence_count"] = len(rows)
    payload["provenance"] = [evidence_json(row) for row in rows]
    return payload


async def list_evidence(
    db: AsyncSession,
    tenant_id: str,
    subject_id: str,
    *,
    field_key: str | None = None,
    include_inactive: bool = True,
) -> list[dict[str, Any]]:
    await _subject(db, tenant_id, subject_id)
    query = select(ScopedCompanyEvidence).where(
        ScopedCompanyEvidence.tenant_id == tenant_id,
        ScopedCompanyEvidence.subject_id == subject_id,
    )
    if field_key:
        query = query.where(ScopedCompanyEvidence.field_key == field_key)
    if not include_inactive:
        query = query.where(
            ScopedCompanyEvidence.superseded.is_(False),
            ScopedCompanyEvidence.stale.is_(False),
        )
    rows = list((await db.scalars(query.order_by(ScopedCompanyEvidence.observed_at.desc()))).all())
    return [evidence_json(row) for row in rows]


async def mark_evidence_inactive(
    db: AsyncSession,
    tenant_id: str,
    subject_id: str,
    evidence_id: str,
    *,
    actor_user_id: str,
    state: str,
) -> dict[str, Any]:
    subject = await _subject(db, tenant_id, subject_id)
    row = await db.scalar(
        select(ScopedCompanyEvidence).where(
            ScopedCompanyEvidence.id == evidence_id,
            ScopedCompanyEvidence.tenant_id == tenant_id,
            ScopedCompanyEvidence.subject_id == subject.id,
        )
    )
    if row is None:
        raise LookupError("Company evidence not found")
    if state == "stale":
        row.stale = True
    elif state == "superseded":
        row.superseded = True
    else:
        raise ValueError("Evidence state must be stale or superseded")

    await append_event(
        db,
        tenant_id=tenant_id,
        event_type=f"company.evidence.{state}",
        payload={
            "subject_id": subject.id,
            "evidence_id": row.id,
            "field": row.field_key,
        },
        actor_type="owner",
        actor_id=actor_user_id,
        source="company_intelligence",
    )
    await synthesize_profile(
        db,
        tenant_id,
        subject_kind=subject.kind,
        subject_reference=subject.reference_id,
        subject_name=subject.display_name,
    )
    return evidence_json(row)


async def resolve_conflict(
    db: AsyncSession,
    tenant_id: str,
    subject_id: str,
    field_key: str,
    winning_evidence_id: str,
    *,
    actor_user_id: str,
) -> dict[str, Any]:
    subject = await _subject(db, tenant_id, subject_id)
    rows = list(
        (
            await db.scalars(
                select(ScopedCompanyEvidence).where(
                    ScopedCompanyEvidence.tenant_id == tenant_id,
                    ScopedCompanyEvidence.subject_id == subject.id,
                    ScopedCompanyEvidence.field_key == field_key,
                    ScopedCompanyEvidence.stale.is_(False),
                )
            )
        ).all()
    )
    if not rows:
        raise LookupError("No evidence exists for this subject field")
    winner = next((row for row in rows if row.id == winning_evidence_id), None)
    if winner is None:
        raise ValueError("Selected evidence does not belong to this subject field")

    # This is explicit owner confirmation of this exact fact, not merely proof that
    # the owner initiated the workflow that produced it.
    winner.stale = False
    winner.superseded = False
    winner.owner_confirmed = True
    superseded_ids: list[str] = []
    for row in rows:
        if row.id == winner.id or row.value_json == winner.value_json:
            continue
        if not row.superseded:
            row.superseded = True
            superseded_ids.append(row.id)

    profile = await synthesize_profile(
        db,
        tenant_id,
        subject_kind=subject.kind,
        subject_reference=subject.reference_id,
        subject_name=subject.display_name,
    )
    await append_event(
        db,
        tenant_id=tenant_id,
        event_type="company.profile.conflict_resolved",
        payload={
            "subject_id": subject.id,
            "subject_kind": subject.kind,
            "subject_reference": subject.reference_id,
            "field": field_key,
            "winning_evidence_id": winner.id,
            "superseded_evidence_ids": superseded_ids,
        },
        actor_type="owner",
        actor_id=actor_user_id,
        source="company_intelligence",
    )
    return {
        "subject": subject_json(subject),
        "field": field_key,
        "winner": evidence_json(winner),
        "superseded_evidence_ids": superseded_ids,
        "profile": profile,
    }
