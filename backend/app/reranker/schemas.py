from pydantic import BaseModel, ConfigDict, Field, field_validator


class RerankCandidateRequest(BaseModel):
    candidate_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=5_000)


class RerankRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2_000)
    limit: int = Field(ge=1, le=20)
    candidates: list[RerankCandidateRequest] = Field(min_length=1, max_length=20)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query must not be blank")
        return value

    @field_validator("candidates")
    @classmethod
    def validate_candidates(
        cls, value: list[RerankCandidateRequest]
    ) -> list[RerankCandidateRequest]:
        candidate_ids = [candidate.candidate_id for candidate in value]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("candidate IDs must be unique")
        return value


class RerankResultResponse(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    candidate_id: str
    rank: int = Field(ge=1)
    score: float


class RerankResponse(BaseModel):
    model: str
    items: list[RerankResultResponse]
    latency_ms: int = Field(ge=0)
