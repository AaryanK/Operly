import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from packages.capabilities.firewall import CapabilityInvocationResult
from packages.connectors.google_provider import CALENDAR
from packages.context.broker import ContextRef
from packages.context.federation import FederatedHistoryService
from packages.context.history_adapters import (
    ProviderHistoryAdapter,
    ProviderHistoryHit,
    ProviderHistoryRegistry,
)
from packages.database.account_connector_models import AccountConnector
from packages.database.db import Base
from packages.database.models import AppUser
from packages.database.schema import import_all_models
from packages.security.execution_context import PERSONAL_EXECUTION_PERMISSIONS
from packages.security.surfaces import SurfaceKind


class _FakeHistoryAdapter(ProviderHistoryAdapter):
    id = "fake.provider"
    source = "fake"
    required_permissions = frozenset({"messages:read"})

    def matches_ref(self, ref: str) -> bool:
        return ref.startswith("fake:")

    async def search(self, runtime_context, *, user_id, surface, conversation_id, query, limit):
        del runtime_context, user_id, surface, conversation_id, query, limit
        return [
            ProviderHistoryHit(
                ref=ContextRef(
                    id="fake:1",
                    source="fake",
                    scope="provider:fake:1",
                    visibility="private",
                    kind="record",
                    description="fake record",
                    estimated_tokens=1,
                ),
                text="fake record",
            )
        ]

    async def materialize(self, runtime_context, *, user_id, surface, conversation_id, refs):
        del runtime_context, user_id, surface, conversation_id
        return {ref: {"ref": ref, "source": "fake"} for ref in refs}


class ProviderHistoryAdapterRegistryTests(unittest.TestCase):
    def test_registry_is_permission_and_surface_gated(self):
        registry = ProviderHistoryRegistry()
        adapter = _FakeHistoryAdapter()
        registry.register(adapter)

        self.assertEqual(registry.get("fake.provider"), adapter)
        self.assertEqual(
            registry.eligible(
                surface=SurfaceKind.PERSONAL_PRIVATE,
                authority={"messages:read"},
                user_id="user-1",
            ),
            (adapter,),
        )
        self.assertEqual(
            registry.eligible(
                surface=SurfaceKind.PERSONAL_PRIVATE,
                authority=set(),
                user_id="user-1",
            ),
            (),
        )
        self.assertEqual(
            registry.eligible(
                surface=SurfaceKind.WORKSPACE_SHARED,
                authority={"messages:read"},
                user_id="user-1",
            ),
            (),
        )

    def test_default_registry_exposes_provider_contract_not_federation_branches(self):
        ids = {adapter.id for adapter in FederatedHistoryService.provider_history_registry().all()}
        self.assertIn("google.gmail", ids)
        self.assertIn("google.calendar", ids)


class GoogleCalendarFederationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_calendar_history_fans_out_across_all_authorized_google_accounts(self):
        async with self.sessions() as db:
            user = AppUser(email="calendar@example.test", display_name="Calendar Human", active=True)
            db.add(user)
            await db.flush()
            user_id = user.id
            first = AccountConnector(
                user_id=user_id,
                connector_type="google_workspace",
                provider="google",
                display_name="Personal Calendar",
                status="connected",
                enabled=True,
                provider_account_id="personal@example.test",
                granted_scopes_json=json.dumps([CALENDAR]),
            )
            second = AccountConnector(
                user_id=user_id,
                connector_type="google_workspace",
                provider="google",
                display_name="Research Calendar",
                status="connected",
                enabled=True,
                provider_account_id="research@example.test",
                granted_scopes_json=json.dumps([CALENDAR]),
            )
            db.add_all([first, second])
            await db.flush()
            connector_names = {first.id: first.display_name, second.id: second.display_name}
            await db.commit()

        async with self.sessions() as db:
            runtime = SimpleNamespace(
                db=db,
                invocation={
                    "channel": "web",
                    "surface": SurfaceKind.PERSONAL_PRIVATE.value,
                    "authority": sorted(PERSONAL_EXECUTION_PERMISSIONS),
                    "metadata": {
                        "_surface_kind": SurfaceKind.PERSONAL_PRIVATE.value,
                        "is_direct": True,
                        "shared_surface": False,
                    },
                },
            )

            async def invoke(request, execution):
                self.assertEqual(execution.user_id, user_id)
                connector_id = request.arguments["connector_id"]
                account_name = connector_names[connector_id]
                return CapabilityInvocationResult(
                    ok=True,
                    capability_id=request.capability_id,
                    status="VERIFIED",
                    observation={
                        "connector_id": connector_id,
                        "provider_account_id": f"{connector_id}@example.test",
                        "account_display_name": account_name,
                        "calendar_id": "primary",
                        "query": request.arguments["query"],
                        "events": [
                            {
                                "id": f"event-{connector_id}",
                                "summary": f"Falcon review from {account_name}",
                                "description": f"Falcon calendar history in {account_name}",
                                "location": "Lab",
                                "start": {"dateTime": "2026-08-26T12:00:00-05:00"},
                                "end": {"dateTime": "2026-08-26T13:00:00-05:00"},
                                "attendees": [],
                                "status": "confirmed",
                            }
                        ],
                    },
                )

            mocked = AsyncMock(side_effect=invoke)
            with patch.object(FederatedHistoryService._calendar_firewall, "invoke", new=mocked):
                refs = await FederatedHistoryService.search(
                    runtime,
                    tenant_id=f"personal:{user_id}",
                    user_id=user_id,
                    conversation_id=None,
                    authority=set(PERSONAL_EXECUTION_PERMISSIONS),
                    surface=SurfaceKind.PERSONAL_PRIVATE,
                    query="falcon",
                    limit=20,
                )

            self.assertEqual(mocked.await_count, 2)
            searched_connector_ids = {
                call.args[0].arguments["connector_id"] for call in mocked.await_args_list
            }
            self.assertEqual(searched_connector_ids, set(connector_names))
            calendar_refs = [ref for ref in refs if ref.source == "google_calendar"]
            self.assertEqual(len(calendar_refs), 2)
            self.assertEqual(
                {ref.scope for ref in calendar_refs},
                {f"provider:google:{connector_id}" for connector_id in connector_names},
            )

    async def test_calendar_provider_ref_without_permission_is_not_materialized(self):
        async with self.sessions() as db:
            user = AppUser(email="calendar@example.test", display_name="Calendar Human", active=True)
            db.add(user)
            await db.flush()
            user_id = user.id
            connector = AccountConnector(
                user_id=user_id,
                connector_type="google_workspace",
                provider="google",
                display_name="Calendar",
                status="connected",
                enabled=True,
                provider_account_id="calendar@example.test",
                granted_scopes_json=json.dumps([CALENDAR]),
            )
            db.add(connector)
            await db.flush()
            ref = f"google_calendar_event:{connector.id}:primary:event-1"
            await db.commit()

        async with self.sessions() as db:
            runtime = SimpleNamespace(db=db, invocation={"channel": "web", "metadata": {}})
            rows = await FederatedHistoryService.materialize(
                runtime,
                refs=[ref],
                tenant_id=f"personal:{user_id}",
                user_id=user_id,
                conversation_id=None,
                authority={"messaging:read"},
                surface=SurfaceKind.PERSONAL_PRIVATE,
            )
            self.assertEqual(rows, [])
