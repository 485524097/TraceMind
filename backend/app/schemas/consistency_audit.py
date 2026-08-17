from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

AuditScope = Literal["knowledge_base", "global"]
AuditStatus = Literal["completed", "partial"]
AuditSeverity = Literal["INFO", "WARNING", "ERROR", "CRITICAL"]
AuditDetailValue = str | int | bool | None


class StrictAuditModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConsistencyAuditFinding(StrictAuditModel):
    finding_id: UUID = Field(default_factory=uuid4)
    code: str
    severity: AuditSeverity
    entity_type: str
    entity_id: str
    knowledge_base_id: UUID | None
    safe_message: str
    details: dict[str, AuditDetailValue]


class ConsistencyAuditSummary(StrictAuditModel):
    healthy: bool
    warning_count: int
    error_count: int
    critical_count: int


class ConsistencyAuditResponse(StrictAuditModel):
    audit_id: UUID
    scope: AuditScope
    status: AuditStatus
    knowledge_base_id: UUID | None
    started_at: datetime
    completed_at: datetime
    summary: ConsistencyAuditSummary
    findings: list[ConsistencyAuditFinding]
