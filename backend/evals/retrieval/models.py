from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Split = Literal["dev", "test"]
Difficulty = Literal["easy", "medium", "hard"]
Strategy = Literal["dense", "hybrid"]
QueryType = Literal[
    "semantic",
    "config_exact",
    "error_code",
    "short_query",
    "multi_evidence",
    "concept_disambiguation",
    "unanswerable",
]


class GoldEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_name: str = Field(min_length=1)
    section_title: str = Field(min_length=1)
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    anchor_text: str = Field(min_length=1, max_length=300)
    relevance: Literal[0, 1, 2]
    required: bool
    notes: str = ""

    @model_validator(mode="after")
    def validate_line_range(self) -> GoldEvidence:
        if self.line_end < self.line_start:
            raise ValueError("line_end must not be smaller than line_start")
        return self


class RetrievalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^ret-\d{3}$")
    split: Split
    query: str = Field(min_length=1, max_length=2_000)
    query_type: QueryType
    difficulty: Difficulty
    language_filter: str | None = None
    answerable: bool
    gold_evidence: list[GoldEvidence]
    absence_terms: list[str] = Field(default_factory=list)
    notes: str = ""

    @field_validator("query")
    @classmethod
    def strip_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query must not be blank")
        return value

    @model_validator(mode="after")
    def validate_answerability(self) -> RetrievalCase:
        positive = [item for item in self.gold_evidence if item.relevance > 0]
        if self.answerable and not positive:
            raise ValueError("answerable cases require positive gold evidence")
        if not self.answerable and self.gold_evidence:
            raise ValueError("unanswerable cases must not contain gold evidence")
        if self.answerable and self.absence_terms:
            raise ValueError("answerable cases must not declare absence terms")
        if not self.answerable and not self.absence_terms:
            raise ValueError("unanswerable cases require absence terms")
        if self.query_type == "multi_evidence":
            required = [item for item in positive if item.required]
            if len(required) < 2:
                raise ValueError(
                    "multi-evidence cases require at least two required evidence items"
                )
        return self


class CorpusManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_version: str
    corpus_filename: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    document_description: str
    expected_section_count: int = Field(gt=0)
    expected_question_count: int = Field(gt=0)
    chunking_snapshot: dict[str, str | int]
    embedding_snapshot: dict[str, str | int]
    notes: str


class RetrievalHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: int = Field(ge=1)
    score: float
    chunk_id: str
    document_name: str
    section_title: str | None
    start_line: int | None
    end_line: int | None
    content: str


class CaseEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    query: str
    strategy: Strategy
    latency_ms: float = Field(ge=0)
    hits: list[RetrievalHit]
    matched_evidence: list[int]
    missing_required_evidence: list[int]
    hit_at_1: float
    hit_at_5: float
    precision_at_5: float
    recall_at_5: float | None
    mrr_at_5: float | None
    ndcg_at_5: float | None
    all_required_at_5: float | None
    passed: bool | None
    observational: bool
    error: str | None = None


class UnanswerableObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    returned_count: int
    top1_score: float | None
    top1_document: str | None
    top1_section: str | None
    top1_content_preview: str | None


class EvaluationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: Strategy
    generated_at: datetime
    case_count: int
    answerable_count: int
    passed_count: int
    failed_count: int
    hit_at_1: float
    hit_at_5: float
    precision_at_5: float
    recall_at_5: float
    mrr_at_5: float
    ndcg_at_5: float
    all_required_at_5: float
    p50_latency_ms: float
    p95_latency_ms: float
    cases: list[CaseEvaluation]
    unanswerable_observations: list[UnanswerableObservation]


class RegressionThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recall_at_5_drop: float = Field(default=0.02, ge=0)
    mrr_at_5_drop: float = Field(default=0.03, ge=0)
    all_required_at_5_drop: float = Field(default=0.02, ge=0)
    hit_at_1_drop: float = Field(default=0.05, ge=0)
    p95_latency_increase_ratio: float = Field(default=0.50, ge=0)


class RegressionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    failures: list[str]
    warnings: list[str]
    deltas: dict[str, float]
