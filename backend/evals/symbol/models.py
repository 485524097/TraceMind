from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ScopeMode = Literal["none", "exact", "fallback"]
ScopeReason = Literal["not_found", "ambiguous", "unsupported"]


class SymbolEvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^sym-\d{3}$")
    query: str = Field(min_length=1, max_length=2_000)
    document_scope_path: str | None
    expected_symbol_scope_mode: ScopeMode
    expected_symbol_scope_reason: ScopeReason | None
    expected_symbol_kind: str | None
    expected_scoped_symbol_qualified_name: str | None
    expected_scoped_symbol_signature: str | None
    expected_relative_path: str | None
    required_result_signatures: list[str]
    forbidden_symbol_signatures: list[str]
    expected_ranking_mode: str | None
    target_in_top_k: bool
    notes: str


class SymbolHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relative_path: str
    symbol_kind: str | None
    symbol_qualified_name: str | None
    symbol_signature: str | None
    ranking_mode: str | None
    start_line: int | None
    end_line: int | None


class SymbolCaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    query: str
    passed: bool
    failures: list[str]
    latency_ms: float
    symbol_scope_mode: str
    symbol_scope_reason: str | None
    scoped_symbol_kind: str | None
    scoped_symbol_qualified_name: str | None
    scoped_symbol_signature: str | None
    hits: list[SymbolHit]


class SymbolEvaluationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_count: int
    passed_count: int
    case_pass_rate: float
    scope_resolution_accuracy: float
    exact_target_recall_at_5: float
    signature_exclusion_accuracy: float
    fallback_reason_accuracy: float
    negative_trigger_accuracy: float
    path_disambiguation_accuracy: float
    p95_latency_ms: float
    cleanup_succeeded: bool
    cases: list[SymbolCaseResult]


class SymbolBaseline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_pass_rate: float = Field(ge=0, le=1)
    scope_resolution_accuracy: float = Field(ge=0, le=1)
    exact_target_recall_at_5: float = Field(ge=0, le=1)
    signature_exclusion_accuracy: float = Field(ge=0, le=1)
    fallback_reason_accuracy: float = Field(ge=0, le=1)
    negative_trigger_accuracy: float = Field(ge=0, le=1)
    path_disambiguation_accuracy: float = Field(ge=0, le=1)
    p95_latency_ms_hard_limit: float | None = Field(default=None, gt=0)
