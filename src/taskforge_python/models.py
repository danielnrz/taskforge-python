from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobType(StrEnum):
    HTTP_FETCH = "http_fetch"
    DOWNLOAD_FILE = "download_file"
    FILE_CHECKSUM = "file_checksum"
    CSV_SUMMARY = "csv_summary"


@dataclass
class Job:
    job_type: JobType
    payload: dict[str, object]
    id: UUID = field(default_factory=uuid4)
    status: JobStatus = JobStatus.QUEUED
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    attempt: int = 0
    result: object | None = None
    error: str | None = None
    worker_id: str | None = None
    error_type: str | None = None

    def mark_running(self, worker_id: str) -> None:
        if self.status not in {JobStatus.QUEUED, JobStatus.RETRYING}:
            raise ValueError("Only queued or retrying jobs can start")

        self.status = JobStatus.RUNNING
        if self.started_at is None:
            self.started_at = datetime.now(UTC)
        self.attempt += 1
        self.worker_id = worker_id
        self.error = None

    def mark_succeeded(self, result: object | None = None) -> None:
        if self.status != JobStatus.RUNNING:
            raise ValueError("Only running jobs can succeed")
        self.status = JobStatus.SUCCEEDED
        self.completed_at = datetime.now(UTC)
        self.result = result
        self.error = None

    def mark_failed(self, error: str) -> None:
        if self.status != JobStatus.RUNNING:
            raise ValueError("Only running jobs can fail")
        self.status = JobStatus.FAILED
        self.completed_at = datetime.now(UTC)
        self.error = error

    def mark_cancelled(self) -> None:
        if self.status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}:
            raise ValueError("Finished jobs cannot be cancelled")

        self.status = JobStatus.CANCELLED
        self.completed_at = datetime.now(UTC)

    def mark_retrying(self, error: str) -> None:
        if self.status != JobStatus.RUNNING:
            raise ValueError("Only running jobs can retry")

        self.status = JobStatus.RETRYING
        self.error = error
        self.worker_id = None
