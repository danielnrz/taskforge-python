import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from taskforge_python.exceptions import JobNotFoundError
from taskforge_python.models import JobStatus
from taskforge_python.repository import JobRepository
from taskforge_python.schemas import JobCreate, JobResponse, MetricsResponse, WorkerResponse
from taskforge_python.service import JobService


def create_app(service: JobService | None = None) -> FastAPI:
    """Create an app whose lifespan owns the worker and database resources."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if service is None:
        database_url = os.getenv("TASKFORGE_DATABASE_URL", "sqlite+aiosqlite:///data/taskforge.db")
        if not database_url.startswith("sqlite+aiosqlite:"):
            raise ValueError("TaskForge V1 requires SQLite with aiosqlite")
        if database_url == "sqlite+aiosqlite:///data/taskforge.db":
            Path("data").mkdir(exist_ok=True)
        service = JobService(
            JobRepository(database_url), worker_count=int(os.getenv("TASKFORGE_WORKERS", "3"))
        )
    jobs = service

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            await jobs.start()
            yield
        finally:
            await jobs.stop()

    app = FastAPI(
        title="TaskForge",
        version="1.0.0",
        lifespan=lifespan,
        description="A local async job processing and monitoring platform.",
    )

    @app.exception_handler(JobNotFoundError)
    async def not_found(request: Request, exc: JobNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.post("/jobs", response_model=JobResponse, status_code=202)
    async def submit_job(body: JobCreate) -> JobResponse:
        job = await jobs.submit(body.job_type, body.payload)
        return JobResponse.model_validate(job)

    @app.get("/jobs", response_model=list[JobResponse])
    async def list_jobs(
        status: JobStatus | None = None,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ) -> list[JobResponse]:
        return [JobResponse.model_validate(job) for job in await jobs.list_jobs(status, limit)]

    @app.get("/jobs/{job_id}", response_model=JobResponse)
    async def get_job(job_id: UUID) -> JobResponse:
        return JobResponse.model_validate(await jobs.get(job_id))

    @app.delete("/jobs/{job_id}", response_model=JobResponse)
    async def cancel_job(job_id: UUID) -> JobResponse:
        try:
            job = await jobs.cancel(job_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JobResponse.model_validate(job)

    @app.get("/workers", response_model=list[WorkerResponse])
    async def workers() -> list[WorkerResponse]:
        return [WorkerResponse.model_validate(worker) for worker in jobs.workers()]

    @app.get("/health")
    async def health() -> dict[str, str | int]:
        healthy = jobs.running and all(not task.done() for task in jobs.worker_tasks)
        if not healthy:
            raise HTTPException(status_code=503, detail="Workers are unavailable")
        return {"status": "ok", "workers": jobs.worker_count}

    @app.get("/metrics", response_model=MetricsResponse)
    async def metrics() -> MetricsResponse:
        return MetricsResponse.model_validate(await jobs.metrics())

    return app
