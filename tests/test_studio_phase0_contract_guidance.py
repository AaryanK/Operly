from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from packages.coding_harness.contract_guidance import (
    generation_contract_packets,
    relational_migration_contract_packet,
    source_contract_repair_packet,
)
from packages.coding_harness.runtime_resolution import RuntimeResolutionError, validate_runtime_contract
from packages.coding_harness.source_service import _plan_specification
from packages.relational_data.contracts import RELATIONAL_CAPABILITY_ID, RelationalMigration


def test_relational_machine_contract_is_derived_from_canonical_validator():
    packet = relational_migration_contract_packet()

    assert packet["capabilityId"] == RELATIONAL_CAPABILITY_ID
    assert packet["migrationJsonSchema"] == RelationalMigration.model_json_schema()
    validated = RelationalMigration.model_validate(packet["canonicalMigrationExample"])
    create_table = validated.operations[0]
    assert create_table.op == "create_table"
    assert create_table.table == "records"
    assert [column.name for column in create_table.columns] == ["id", "occurred_at"]


def test_relational_contract_packet_calls_out_the_live_failure_shape():
    packet = source_contract_repair_packet(
        "Invalid relational migration migrations/001_init.json: "
        "CreateTable.columns Field required; CreateTable.type Extra inputs are not permitted"
    )

    assert packet["classification"] == "relational_migration_contract_failure"
    relational = packet["contracts"][RELATIONAL_CAPABILITY_ID]
    assert "columns" in json.dumps(relational["migrationJsonSchema"])
    assert any("never a create_table field" in rule for rule in relational["repairRules"])


def test_unrelated_runtime_failures_do_not_receive_relational_noise():
    assert source_contract_repair_packet("static web is missing index.html") == {}


def test_runtime_resolution_appends_machine_repair_packet_for_relational_failure():
    class RejectingRegistry:
        def resolve(self, _bundle):
            raise ValueError(
                "Runtime operly-fullstack-v1 rejected source: Invalid relational migration "
                "migrations/001_init.json: CreateTable.columns Field required"
            )

    with patch("packages.coding_harness.runtime_resolution._registry", return_value=RejectingRegistry()):
        with pytest.raises(RuntimeResolutionError) as raised:
            validate_runtime_contract(object())

    message = str(raised.value)
    assert "OPERLY_CONTRACT_REPAIR_PACKET=" in message
    encoded = message.split("OPERLY_CONTRACT_REPAIR_PACKET=", 1)[1]
    repair = json.loads(encoded)
    example = repair["contracts"][RELATIONAL_CAPABILITY_ID]["canonicalMigrationExample"]
    RelationalMigration.model_validate(example)


def test_source_generation_receives_machine_contract_before_first_model_turn():
    class MinimalPlan:
        def model_dump(self, mode="json"):
            assert mode == "json"
            return {
                "projectName": "Attendance",
                "summary": "Track clock in and clock out events",
                "requirementLedger": [],
                "planTree": [],
                "globalValidation": {},
                "unsupportedRequirements": [],
            }

    specification = json.loads(_plan_specification(MinimalPlan()))
    execution = specification["operlyExecutionContract"]
    assert execution["machineContracts"] == generation_contract_packets()
    relational = execution["machineContracts"][RELATIONAL_CAPABILITY_ID]
    RelationalMigration.model_validate(relational["canonicalMigrationExample"])
    assert "authoritative" in execution["contractAuthority"]
