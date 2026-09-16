from __future__ import annotations

from datetime import datetime, timedelta
import json
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.database.account_connector_models import AccountConnector
from packages.database.db import Base
from packages.database.kernel_models import KernelApproval, KernelRequestClaim
from packages.database.models import AppUser
from packages.database.schema import import_all_models
from packages.kernel.approvals import APPROVAL_TTL, approval_json, decide_approval
from packages.kernel.contracts import RuntimeRequest
from packages.kernel.providers import ProviderExecutionUncertain
from packages.kernel.runtime import RuntimeExecutionError
from packages.personal_modules.google_provider import GMAIL_MODIFY
from packages.personal_modules.runtime import build_personal_runtime
from packages.security.execution_context import (
    ExecutionContext,
    PERSONAL_EXECUTION_PERMISSIONS,
    ScopeKind,
)
from packages.security.surfaces import SurfaceKind


ARGS = {
    "to": ["alex@example.test"],
    "subject": "Tuesday at 2?",
    "text_body": "Hi Alex,\n\nWould Tuesday at 2:00 PM work for you?",
}


class PersonalApprovedSendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions() as db:
            db.add(AppUser(id="user-alpha", email="alpha@example.test", display_name="Alpha"))
            db.add(
                AccountConnector(
                    id="google-alpha",
                    user_id="user-alpha",
                    connector_type="google_account",
                    provider="google",
                    display_name="Personal Google",
                    status="connected",
                    enabled=True,
                    provider_account_id="alpha@example.test",
                    granted_scopes_json=json.dumps([GMAIL_MODIFY]),
                )
            )
            await db.commit()
        self.runtime = build_personal_runtime()
        self.context = self._context()

    async def asyncTearDown(self):
        await self.engine.dispose()

    def _context(self, *, allow_send: bool = True) -> ExecutionContext:
        permissions = set(PERSONAL_EXECUTION_PERMISSIONS)
        if not allow_send:
            permissions.discard("messaging:send")
        return ExecutionContext(
            workspace_id=None,
            user_id="user-alpha",
            membership_id=None,
            role="personal_owner",
            permissions=frozenset(permissions),
            channel="web",
            surface=SurfaceKind.PERSONAL_PRIVATE,
            conversation_id="conversation-alpha",
            scope_kind=ScopeKind.PERSONAL,
            principal_id="user:user-alpha",
            workspace_mode="personal",
        )

    def _request(
        self,
        request_id: str,
        *,
        arguments: dict | None = None,
        approval_id: str | None = None,
    ) -> RuntimeRequest:
        return RuntimeRequest(
            goal="Send the reviewed email to Alex",
            capability_id="google.gmail.send_email",
            arguments=dict(arguments or ARGS),
            conversation_id="conversation-alpha",
            request_id=request_id,
            approval_id=approval_id,
        )

    async def _pending_approval(self, request_id: str, *, arguments: dict | None = None) -> KernelApproval:
        async with self.sessions() as db:
            with self.assertRaises(RuntimeExecutionError) as caught:
                await self.runtime.execute(
                    db,
                    context=self.context,
                    request=self._request(request_id, arguments=arguments),
                )
            self.assertEqual(caught.exception.code, "approval_required")
            self.assertIsNotNone(caught.exception.approval_id)
            row = await db.get(KernelApproval, caught.exception.approval_id)
            self.assertIsNotNone(row)
            return row

    async def _approve(self, approval_id: str) -> KernelApproval:
        async with self.sessions() as db:
            row = await decide_approval(
                db,
                context=self.context,
                approval_id=approval_id,
                approved=True,
                decided_by_user_id="user-alpha",
            )
            await db.commit()
            return row

    async def test_approval_exposes_exact_review_hash_and_expiry(self):
        approval = await self._pending_approval("send-review-1")
        payload = approval_json(approval, include_arguments=True)
        self.assertEqual(payload["arguments"], {**ARGS, "cc": [], "bcc": []})
        self.assertEqual(payload["arguments_hash"], approval.arguments_hash)
        self.assertTrue(payload["expires_at"])
        self.assertEqual(payload["status"], "pending")

    async def test_rejected_or_changed_approval_never_sends(self):
        approval = await self._pending_approval("send-denied-1")
        async with self.sessions() as db:
            await decide_approval(
                db,
                context=self.context,
                approval_id=approval.id,
                approved=False,
                decided_by_user_id="user-alpha",
            )
            await db.commit()
        send = AsyncMock(side_effect=AssertionError("denied approval must not send"))
        with patch("packages.personal_modules.reliable_google_provider._gmail_send_once", send):
            async with self.sessions() as db:
                with self.assertRaises(RuntimeExecutionError) as caught:
                    await self.runtime.execute(
                        db,
                        context=self.context,
                        request=self._request("send-denied-1", approval_id=approval.id),
                    )
        self.assertEqual(caught.exception.code, "approval_invalid")
        send.assert_not_awaited()

        changed = await self._pending_approval("send-changed-1")
        await self._approve(changed.id)
        edited = dict(ARGS)
        edited["text_body"] = ARGS["text_body"] + "\nChanged after review."
        with patch("packages.personal_modules.reliable_google_provider._gmail_send_once", send):
            async with self.sessions() as db:
                with self.assertRaises(RuntimeExecutionError) as changed_error:
                    await self.runtime.execute(
                        db,
                        context=self.context,
                        request=self._request(
                            "send-changed-1",
                            arguments=edited,
                            approval_id=changed.id,
                        ),
                    )
        self.assertEqual(changed_error.exception.code, "approval_invalid")
        send.assert_not_awaited()

    async def test_expired_or_revoked_authority_blocks_before_send(self):
        approval = await self._pending_approval("send-expired-1")
        await self._approve(approval.id)
        async with self.sessions() as db:
            row = await db.get(KernelApproval, approval.id)
            row.created_at = datetime.utcnow() - APPROVAL_TTL - timedelta(seconds=1)
            await db.commit()
        send = AsyncMock(side_effect=AssertionError("expired approval must not send"))
        with patch("packages.personal_modules.reliable_google_provider._gmail_send_once", send):
            async with self.sessions() as db:
                with self.assertRaises(RuntimeExecutionError) as expired:
                    await self.runtime.execute(
                        db,
                        context=self.context,
                        request=self._request("send-expired-1", approval_id=approval.id),
                    )
        self.assertEqual(expired.exception.code, "approval_invalid")
        send.assert_not_awaited()

        authority = await self._pending_approval("send-revoked-1")
        await self._approve(authority.id)
        with patch("packages.personal_modules.reliable_google_provider._gmail_send_once", send):
            async with self.sessions() as db:
                with self.assertRaises(RuntimeExecutionError) as revoked:
                    await self.runtime.execute(
                        db,
                        context=self._context(allow_send=False),
                        request=self._request("send-revoked-1", approval_id=authority.id),
                    )
        self.assertEqual(revoked.exception.code, "forbidden")
        send.assert_not_awaited()

    async def test_success_persists_intent_before_send_and_replay_does_not_resend(self):
        approval = await self._pending_approval("send-success-1")
        await self._approve(approval.id)
        observed: dict = {}

        async def send_once(token: str, *, raw_message: str):
            self.assertEqual(token, "fixture-token")
            self.assertTrue(raw_message)
            async with self.sessions() as inspect_db:
                claim = await inspect_db.scalar(
                    select(KernelRequestClaim).where(
                        KernelRequestClaim.request_id == "send-success-1"
                    )
                )
                observed["status"] = claim.status
                observed.update(json.loads(claim.response_json))
            return {"id": "gmail-message-1", "threadId": "gmail-thread-1"}

        send = AsyncMock(side_effect=send_once)
        verify = AsyncMock(return_value={"id": "gmail-message-1", "threadId": "gmail-thread-1"})
        with patch(
            "packages.personal_modules.reliable_google_provider.access_token",
            new=AsyncMock(return_value="fixture-token"),
        ), patch(
            "packages.personal_modules.reliable_google_provider._gmail_send_once",
            new=send,
        ), patch(
            "packages.personal_modules.reliable_google_provider._verify_provider_message",
            new=verify,
        ):
            async with self.sessions() as db:
                result = await self.runtime.execute(
                    db,
                    context=self.context,
                    request=self._request("send-success-1", approval_id=approval.id),
                )
            async with self.sessions() as db:
                replay = await self.runtime.execute(
                    db,
                    context=self.context,
                    request=self._request("send-success-1", approval_id=approval.id),
                )

        self.assertEqual(observed["status"], "running")
        self.assertEqual(observed["intent"], "gmail_send")
        self.assertEqual(observed["approval_id"], approval.id)
        self.assertEqual(observed["approval_arguments_hash"], approval.arguments_hash)
        self.assertTrue(observed["rfc822_message_id"].startswith("<operly-"))
        self.assertEqual(result.result["verification_status"], "read_back")
        self.assertEqual(replay.result["message_id"], "gmail-message-1")
        self.assertEqual(send.await_count, 1)
        async with self.sessions() as db:
            claim = await db.scalar(
                select(KernelRequestClaim).where(KernelRequestClaim.request_id == "send-success-1")
            )
            stored_approval = await db.get(KernelApproval, approval.id)
        self.assertEqual(claim.status, "completed")
        self.assertEqual(stored_approval.status, "consumed")

    async def test_timeout_reconciles_by_stable_message_id_without_second_send(self):
        approval = await self._pending_approval("send-reconcile-1")
        await self._approve(approval.id)
        send = AsyncMock(side_effect=ProviderExecutionUncertain("socket closed after POST"))
        reconcile = AsyncMock(return_value={"id": "gmail-reconciled", "threadId": "thread-r"})
        with patch(
            "packages.personal_modules.reliable_google_provider.access_token",
            new=AsyncMock(return_value="fixture-token"),
        ), patch(
            "packages.personal_modules.reliable_google_provider._gmail_send_once",
            new=send,
        ), patch(
            "packages.personal_modules.reliable_google_provider._reconcile_sent_message",
            new=reconcile,
        ):
            async with self.sessions() as db:
                result = await self.runtime.execute(
                    db,
                    context=self.context,
                    request=self._request("send-reconcile-1", approval_id=approval.id),
                )

        self.assertEqual(result.result["provider_status"], "reconciled_sent")
        self.assertEqual(result.result["verification_status"], "read_back")
        self.assertEqual(send.await_count, 1)
        self.assertEqual(reconcile.await_count, 1)

    async def test_unresolved_timeout_is_durable_uncertain_and_blocks_replay(self):
        approval = await self._pending_approval("send-uncertain-1")
        await self._approve(approval.id)
        send = AsyncMock(side_effect=ProviderExecutionUncertain("socket closed after POST"))
        with patch(
            "packages.personal_modules.reliable_google_provider.access_token",
            new=AsyncMock(return_value="fixture-token"),
        ), patch(
            "packages.personal_modules.reliable_google_provider._gmail_send_once",
            new=send,
        ), patch(
            "packages.personal_modules.reliable_google_provider._reconcile_sent_message",
            new=AsyncMock(return_value=None),
        ):
            async with self.sessions() as db:
                with self.assertRaises(RuntimeExecutionError) as uncertain:
                    await self.runtime.execute(
                        db,
                        context=self.context,
                        request=self._request("send-uncertain-1", approval_id=approval.id),
                    )
            self.assertEqual(uncertain.exception.code, "execution_outcome_uncertain")
            self.assertEqual(
                uncertain.exception.details["recovery"],
                "search_sent_mail_by_rfc822_message_id_before_retry",
            )

            async with self.sessions() as db:
                with self.assertRaises(RuntimeExecutionError) as replay:
                    await self.runtime.execute(
                        db,
                        context=self.context,
                        request=self._request("send-uncertain-1", approval_id=approval.id),
                    )

        self.assertEqual(replay.exception.code, "request_in_progress")
        self.assertEqual(send.await_count, 1)
        async with self.sessions() as db:
            claim = await db.scalar(
                select(KernelRequestClaim).where(KernelRequestClaim.request_id == "send-uncertain-1")
            )
            stored_approval = await db.get(KernelApproval, approval.id)
        self.assertEqual(claim.status, "uncertain")
        metadata = json.loads(claim.response_json)
        self.assertEqual(metadata["intent"], "gmail_send")
        self.assertIn("uncertainty_reason", metadata)
        self.assertEqual(stored_approval.status, "executing")


if __name__ == "__main__":
    unittest.main()
