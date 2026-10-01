import asyncio
from dataclasses import asdict
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, String, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from taskforge_python.exceptions import JobNotFoundError
from taskforge_python.models import Job, JobStatus, JobType


class Base(DeclarativeBase):
    pass


class JobRecord(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[str] = mapped_column(String, index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)


def encode_job(job: Job) -> dict[str, Any]:
    data = asdict(job)
    data["id"] = str(job.id)
    for key in ("created_at", "started_at", "completed_at"):
        value = data[key]
        data[key] = value.isoformat() if value is not None else None
    return data


def decode_job(data: dict[str, Any]) -> Job:
    fields = dict(data)
    fields["id"] = UUID(fields["id"])
    fields["job_type"] = JobType(fields["job_type"])
    fields["status"] = JobStatus(fields["status"])
    for key in ("created_at", "started_at", "completed_at"):
        value = fields[key]
        fields[key] = datetime.fromisoformat(value) if value is not None else None
    return Job(**fields)


class JobRepository:
    """Store detached job snapshots; callers never need database sessions."""

    def __init__(self, database_url: str) -> None:
        self.engine = create_async_engine(database_url)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.lock = asyncio.Lock()

    async def initialize(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def save(self, job: Job) -> None:
        data = encode_job(job)
        async with self.lock, self.sessions() as session:
            await session.merge(
                JobRecord(id=str(job.id), created_at=job.created_at.isoformat(), data=data)
            )
            await session.commit()

    async def get(self, job_id: UUID) -> Job:
        async with self.lock, self.sessions() as session:
            record = await session.get(JobRecord, str(job_id))
            if record is None:
                raise JobNotFoundError(f"Job {job_id} was not found")
            return decode_job(record.data)

    async def list(self) -> list[Job]:
        async with self.lock, self.sessions() as session:
            records = await session.scalars(select(JobRecord).order_by(JobRecord.created_at.desc()))
            return [decode_job(record.data) for record in records]

    async def close(self) -> None:
        await self.engine.dispose()
