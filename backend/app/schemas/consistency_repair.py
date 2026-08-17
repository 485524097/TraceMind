from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

RepairItemStatus = Literal[
    "pending",
    "running",
    "planned",
    "succeeded",
    "failed",
    "skipped",
    "not_repairable",
    "verification_failed",
]
RepairOperationStatus = Literal[
    "planned", "queued", "running", "partially_failed", "failed", "succeeded"
]


class StrictRepairModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConsistencyRepairRequest(StrictRepairModel):
    audit_id: UUID
    knowledge_base_id: UUID
    finding_ids: list[UUID] = Field(min_length=1, max_length=100)
    dry_run: bool = True

    @field_validator("finding_ids")
    @classmethod
    def unique_findings(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("finding_ids must be unique")
        return value


class ConsistencyRepairItemResponse(StrictRepairModel):
    finding_id: UUID
    finding_code: str
    entity_type: str
    entity_id: str
    repairable: bool
    status: RepairItemStatus
    action: str
    requires_parse: bool = False
    requires_index: bool = False
    deletes_qdrant_points: bool = False
    cleans_journal: bool = False
    started_at: datetime | None = None
    completed_at: datetime | None = None
    safe_message: str


class ConsistencyRepairResponse(StrictRepairModel):
    knowledge_base_id: UUID
    audit_id: UUID
    operation_id: UUID | None
    dry_run: bool
    status: RepairOperationStatus
    items: list[ConsistencyRepairItemResponse]
    started_at: datetime | None = None
    completed_at: datetime | None = None
