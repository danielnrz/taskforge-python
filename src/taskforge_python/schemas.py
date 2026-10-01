from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, computed_field, model_validator

from taskforge_python.models import JobStatus, JobType
from taskforge_python.payloads import validate_payload


class JobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_type: JobType
    payload: dict[str, Any]

    @model_validator(mode="after")
    def check_payload(self) -> "JobCreate":
        self.payload = validate_payload(self.job_type, self.payload)
        return self


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    job_type: JobType
    payload: dict[str, Any]
    status: JobStatus
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    attempt: int
    result: Any
    error: str | None
    error_type: str | None
    worker_id: str | None

    @computed_field
    def duration(self) -> float | None:
        if self.started_at is None:
            return None
        end = self.completed_at or datetime.now(UTC)
        return max(0, (end - self.started_at).total_seconds())


class WorkerResponse(BaseModel):
    id: str
    state: Literal["idle", "busy", "stopped"]
    job_id: UUID | None


class MetricsResponse(BaseModel):
    total_jobs: int
    queued: int
    running: int
    retrying: int
    succeeded: int
    failed: int
    cancelled: int
    success_rate: float
    average_duration: float
    total_retries: int
    failure_types: dict[str, int]
