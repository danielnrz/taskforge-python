import asyncio
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx

from taskforge_python.async_handlers import AsyncHandlers
from taskforge_python.async_runner import AsyncJobRunner
from taskforge_python.models import Job, JobStatus, JobType
from taskforge_python.payloads import validate_payload
from taskforge_python.repository import JobRepository
from taskforge_python.retry import RetryPolicy

logger = logging.getLogger(__name__)
TERMINAL_STATUSES = {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}


class JobService:
    """Coordinate one process's durable jobs and in-memory worker queue."""

    def __init__(
        self,
        repository: JobRepository,
        worker_count: int = 3,
        client: httpx.AsyncClient | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        if not 1 <= worker_count <= 16:
            raise ValueError("worker_count must be between 1 and 16")
        self.repository = repository
        self.worker_count = worker_count
        self.client = client
        self.owns_client = client is None
        self.retry_policy = retry_policy or RetryPolicy()
        self.queue: asyncio.Queue[UUID] = asyncio.Queue()
        self.worker_tasks: list[asyncio.Task[None]] = []
        self.active_tasks: dict[UUID, asyncio.Task[None]] = {}
        self.current_jobs: dict[str, UUID | None] = {}
        self.running = False
        self.control_lock = asyncio.Lock()

    async def start(self) -> None:
        if self.running:
            return
        await self.repository.initialize()
        for job in await self.repository.list():
            if job.status == JobStatus.QUEUED:
                self.queue.put_nowait(job.id)
            elif job.status in {JobStatus.RUNNING, JobStatus.RETRYING}:
                job.status = JobStatus.FAILED
                job.completed_at = datetime.now(UTC)
                job.error = "Execution interrupted by application shutdown or crash"
                job.error_type = "InterruptedJob"
                await self.repository.save(job)
        if self.client is None:
            self.client = httpx.AsyncClient(follow_redirects=False)
        runner = AsyncJobRunner(AsyncHandlers(self.client), self.retry_policy)
        self.running = True
        self.worker_tasks = []
        for number in range(self.worker_count):
            worker_id = f"worker-{number + 1}"
            self.current_jobs[worker_id] = None
            self.worker_tasks.append(
                asyncio.create_task(self._worker(worker_id, runner), name=worker_id)
            )
        logger.info("event=started workers=%s", self.worker_count)

    async def stop(self) -> None:
        self.running = False
        async with self.control_lock:
            # Finish any existing cancellation before closing shared resources.
            active = list(self.active_tasks.values())
            for task in active:
                if not task.cancelling():
                    task.cancel()
            await asyncio.gather(*active, return_exceptions=True)
            for task in self.worker_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*self.worker_tasks, return_exceptions=True)
        if self.owns_client and self.client is not None:
            await self.client.aclose()
            self.client = None
        await self.repository.close()
        while not self.queue.empty():
            self.queue.get_nowait()
            self.queue.task_done()
        logger.info("event=stopped")

    async def submit(self, job_type: JobType, payload: dict[str, object]) -> Job:
        async with self.control_lock:
            if not self.running:
                raise ValueError("Job service is not running")
            job = Job(job_type, validate_payload(job_type, payload))
            await self.repository.save(job)
            self.queue.put_nowait(job.id)
            logger.info("job=%s event=queued type=%s", job.id, job.job_type)
            return job

    async def get(self, job_id: UUID) -> Job:
        return await self.repository.get(job_id)

    async def list_jobs(self, status: JobStatus | None = None, limit: int = 100) -> list[Job]:
        jobs = await self.repository.list()
        return [job for job in jobs if status is None or job.status == status][:limit]

    async def cancel(self, job_id: UUID) -> Job:
        async with self.control_lock:
            job = await self.get(job_id)
            if job.status in TERMINAL_STATUSES:
                raise ValueError("Finished jobs cannot be cancelled")
            task = self.active_tasks.get(job_id)
            if task is not None:
                if not task.cancelling():
                    task.cancel()
                await asyncio.shield(task)
            else:
                job.mark_cancelled()
                await self.repository.save(job)
            result = await self.get(job_id)
            if result.status != JobStatus.CANCELLED:
                raise ValueError("Job finished before cancellation could take effect")
            return result

    async def _worker(self, worker_id: str, runner: AsyncJobRunner) -> None:
        while self.running:
            job_id = await self.queue.get()
            try:
                # Serialize claiming a job with submission and cancellation.
                async with self.control_lock:
                    job = await self.get(job_id)
                    if job.status != JobStatus.QUEUED:
                        continue
                    self.current_jobs[worker_id] = job_id
                    task = asyncio.create_task(runner.run(job, worker_id, self.repository.save))
                    self.active_tasks[job_id] = task
                await task
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("job=%s worker=%s event=worker_error", job_id, worker_id)
            finally:
                self.active_tasks.pop(job_id, None)
                self.current_jobs[worker_id] = None
                self.queue.task_done()

    def workers(self) -> list[dict[str, Any]]:
        return [
            {
                "id": task.get_name(),
                "state": "stopped"
                if task.done()
                else ("busy" if self.current_jobs[task.get_name()] else "idle"),
                "job_id": str(self.current_jobs[task.get_name()])
                if self.current_jobs[task.get_name()]
                else None,
            }
            for task in self.worker_tasks
        ]

    async def metrics(self) -> dict[str, Any]:
        jobs = await self.repository.list()
        counts = {status.value: sum(job.status == status for job in jobs) for status in JobStatus}
        finished = counts["succeeded"] + counts["failed"]
        durations = [
            (job.completed_at - job.started_at).total_seconds()
            for job in jobs
            if job.completed_at is not None and job.started_at is not None
        ]
        failure_types: dict[str, int] = {}
        for job in jobs:
            if job.status == JobStatus.FAILED:
                error_type = job.error_type or "UnknownError"
                failure_types[error_type] = failure_types.get(error_type, 0) + 1
        return {
            "total_jobs": len(jobs),
            **counts,
            "success_rate": round(counts["succeeded"] / finished * 100, 1) if finished else 0,
            "average_duration": sum(durations) / len(durations) if durations else 0,
            "total_retries": sum(max(0, job.attempt - 1) for job in jobs),
            "failure_types": failure_types,
        }
