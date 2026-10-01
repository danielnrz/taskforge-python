import asyncio
from datetime import UTC

import httpx
import pytest

from taskforge_python.exceptions import JobNotFoundError
from taskforge_python.models import Job, JobStatus, JobType
from taskforge_python.repository import JobRepository
from taskforge_python.retry import RetryPolicy
from taskforge_python.service import JobService


async def wait_status(service, job_id, statuses):
    async with asyncio.timeout(3):
        while True:
            job = await service.get(job_id)
            if job.status in statuses:
                return job
            await asyncio.sleep(0.005)


@pytest.fixture
async def service(tmp_path):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text="ok"))
    ) as client:
        app = JobService(
            JobRepository(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}"),
            worker_count=2,
            client=client,
            retry_policy=RetryPolicy(base_delay=0),
        )
        await app.start()
        yield app
        await app.stop()


async def test_service_completes_and_persists_job(service):
    job = await service.submit(JobType.HTTP_FETCH, {"url": "https://example.com"})
    finished = await wait_status(service, job.id, {JobStatus.SUCCEEDED})
    persisted = await service.repository.get(job.id)
    assert persisted.result["body"] == "ok"
    assert persisted.status == JobStatus.SUCCEEDED
    assert persisted.started_at.tzinfo == UTC
    assert finished.attempt == 1
    await service.queue.join()
    assert all(worker["state"] == "idle" for worker in service.workers())


async def test_unknown_job(service):
    from uuid import uuid4

    with pytest.raises(JobNotFoundError):
        await service.get(uuid4())


async def test_submission_validates_before_queueing(service):
    with pytest.raises(ValueError):
        await service.submit(JobType.HTTP_FETCH, {"url": "bad"})
    assert await service.list_jobs() == []
    assert service.queue.empty()


@pytest.mark.parametrize(
    "statuses,expected,attempts",
    [
        ([503, 200], JobStatus.SUCCEEDED, 2),
        ([503, 503, 503], JobStatus.FAILED, 3),
        ([404], JobStatus.FAILED, 1),
    ],
)
async def test_retries_and_max_attempts(tmp_path, statuses, expected, attempts):
    responses = iter(statuses)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(next(responses), text="body"))
    ) as client:
        service = JobService(
            JobRepository(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}"),
            client=client,
            retry_policy=RetryPolicy(base_delay=0),
        )
        await service.start()
        try:
            job = await service.submit(JobType.HTTP_FETCH, {"url": "https://example.com"})
            result = await wait_status(service, job.id, {JobStatus.SUCCEEDED, JobStatus.FAILED})
            assert result.status == expected
            assert result.attempt == attempts
            if expected == JobStatus.FAILED:
                assert result.error_type in {"RetryableJobError", "PermanentJobError"}
        finally:
            await service.stop()


def test_backoff_is_exponential_and_bounded():
    policy = RetryPolicy(max_attempts=3, base_delay=0.5)
    assert policy.delay(1) == 0.5
    assert policy.delay(2) == 1.0
    for kwargs in [{"max_attempts": 0}, {"base_delay": -1}, {"base_delay": float("nan")}]:
        with pytest.raises(ValueError):
            RetryPolicy(**kwargs)


async def test_workers_execute_concurrently(tmp_path):
    entered = 0
    both_started = asyncio.Event()
    release = asyncio.Event()

    async def request_handler(request):
        nonlocal entered
        entered += 1
        if entered == 2:
            both_started.set()
        await release.wait()
        return httpx.Response(200, text="done")

    async with httpx.AsyncClient(transport=httpx.MockTransport(request_handler)) as client:
        service = JobService(
            JobRepository(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}"),
            worker_count=2,
            client=client,
        )
        await service.start()
        try:
            jobs = [
                await service.submit(JobType.HTTP_FETCH, {"url": "https://example.com"})
                for _ in range(2)
            ]
            await asyncio.wait_for(both_started.wait(), 2)
            assert sum(worker["state"] == "busy" for worker in service.workers()) == 2
            release.set()
            for job in jobs:
                await wait_status(service, job.id, {JobStatus.SUCCEEDED})
        finally:
            release.set()
            await service.stop()


async def test_cancellation_running_and_queued(tmp_path):
    started = asyncio.Event()

    async def request_handler(request):
        started.set()
        await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(request_handler)) as client:
        service = JobService(
            JobRepository(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}"),
            worker_count=1,
            client=client,
        )
        await service.start()
        try:
            first = await service.submit(JobType.HTTP_FETCH, {"url": "https://example.com"})
            await asyncio.wait_for(started.wait(), 2)
            second = await service.submit(JobType.HTTP_FETCH, {"url": "https://example.com"})
            await service.cancel(second.id)
            await service.cancel(first.id)
            await asyncio.wait_for(service.queue.join(), 2)
            assert (await service.get(first.id)).status == JobStatus.CANCELLED
            assert (await service.get(second.id)).attempt == 0
            assert (await service.repository.get(first.id)).status == JobStatus.CANCELLED
            with pytest.raises(ValueError):
                await service.cancel(first.id)
        finally:
            await service.stop()


async def test_cancel_during_backoff(tmp_path):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(503))
    ) as client:
        service = JobService(
            JobRepository(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}"),
            client=client,
            retry_policy=RetryPolicy(base_delay=30),
        )
        await service.start()
        try:
            job = await service.submit(JobType.HTTP_FETCH, {"url": "https://example.com"})
            await wait_status(service, job.id, {JobStatus.RETRYING})
            await asyncio.wait_for(service.cancel(job.id), 1)
            assert (await service.get(job.id)).status == JobStatus.CANCELLED
            assert (await service.get(job.id)).attempt == 1
        finally:
            await service.stop()


async def test_restart_restores_queued_and_fails_interrupted_jobs(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}"
    repository = JobRepository(url)
    await repository.initialize()
    queued = Job(JobType.HTTP_FETCH, {"url": "https://example.com"})
    running = Job(JobType.HTTP_FETCH, {"url": "https://example.com"})
    running.mark_running("old-worker")
    retrying = Job(JobType.HTTP_FETCH, {"url": "https://example.com"})
    retrying.mark_running("old-worker")
    retrying.mark_retrying("timeout")
    for job in [queued, running, retrying]:
        await repository.save(job)
    await repository.close()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200))
    ) as client:
        service = JobService(JobRepository(url), client=client)
        await service.start()
        try:
            await wait_status(service, queued.id, {JobStatus.SUCCEEDED})
            assert (await service.get(running.id)).status == JobStatus.FAILED
            assert (await service.get(retrying.id)).status == JobStatus.FAILED
            assert (await service.get(running.id)).error_type == "InterruptedJob"
        finally:
            await service.stop()


async def test_metrics_counts_durations_and_failures(service):
    success = await service.submit(JobType.HTTP_FETCH, {"url": "https://example.com"})
    failure = await service.submit(JobType.FILE_CHECKSUM, {"path": "/missing/taskforge.txt"})
    await wait_status(service, success.id, {JobStatus.SUCCEEDED})
    await wait_status(service, failure.id, {JobStatus.FAILED})
    metrics = await service.metrics()
    assert metrics["total_jobs"] == 2
    assert metrics["succeeded"] == 1
    assert metrics["failed"] == 1
    assert metrics["success_rate"] == 50.0
    assert metrics["average_duration"] >= 0
    assert metrics["failure_types"] == {"PermanentJobError": 1}


async def test_repository_returns_independent_snapshots(tmp_path):
    repository = JobRepository(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    await repository.initialize()
    try:
        job = Job(JobType.HTTP_FETCH, {"url": "https://example.com"})
        await repository.save(job)
        job.payload["url"] = "changed"
        stored = await repository.get(job.id)
        assert stored.payload == {"url": "https://example.com"}
        assert stored.id == job.id
    finally:
        await repository.close()


async def test_shutdown_stops_active_workers(tmp_path):
    started = asyncio.Event()

    async def request_handler(request):
        started.set()
        await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(request_handler)) as client:
        service = JobService(
            JobRepository(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}"), client=client
        )
        await service.start()
        await service.submit(JobType.HTTP_FETCH, {"url": "https://example.com"})
        await asyncio.wait_for(started.wait(), 2)
        await asyncio.wait_for(service.stop(), 2)
        assert all(worker["state"] == "stopped" for worker in service.workers())
