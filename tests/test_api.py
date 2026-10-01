import asyncio
from uuid import uuid4

import httpx
import pytest

from taskforge_python.api import create_app
from taskforge_python.repository import JobRepository
from taskforge_python.service import JobService


@pytest.fixture
async def client(tmp_path):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text="hello"))
    ) as http:
        service = JobService(
            JobRepository(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}"), client=http
        )
        app = create_app(service)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client,
        ):
            yield client


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"job_type": "unknown", "payload": {}},
        {"job_type": "http_fetch", "payload": {"url": "invalid"}},
        {"job_type": "download_file", "payload": {"url": "https://example.com"}},
        {"job_type": "csv_summary", "payload": {"path": 123}},
        {"job_type": "http_fetch", "payload": {"url": "https://example.com", "timeout": -1}},
    ],
)
async def test_create_rejects_invalid_payload(client, body):
    assert (await client.post("/jobs", json=body)).status_code == 422
    assert (await client.get("/jobs")).json() == []


async def test_create_get_list_metrics_and_conflict(client):
    response = await client.post(
        "/jobs",
        json={
            "job_type": "http_fetch",
            "payload": {"url": "https://example.com"},
        },
    )
    assert response.status_code == 202
    job_id = response.json()["id"]
    async with asyncio.timeout(3):
        while True:
            job = (await client.get(f"/jobs/{job_id}")).json()
            if job["status"] == "succeeded":
                break
            await asyncio.sleep(0.01)
    assert job["result"]["body"] == "hello"
    assert job["duration"] >= 0
    assert (await client.get("/jobs?status=succeeded&limit=1")).json()[0]["id"] == job_id
    assert (await client.get("/jobs?status=failed")).json() == []
    assert (await client.delete(f"/jobs/{job_id}")).status_code == 409
    metrics = (await client.get("/metrics")).json()
    assert metrics["total_jobs"] == 1
    assert metrics["success_rate"] == 100
    assert (await client.get("/health")).json()["status"] == "ok"
    workers = (await client.get("/workers")).json()
    assert len(workers) == 3
    assert {worker["state"] for worker in workers} <= {"idle", "busy"}
    assert (await client.get("/openapi.json")).status_code == 200


@pytest.mark.parametrize("method", ["get", "delete"])
async def test_missing_job_returns_404(client, method):
    assert (await getattr(client, method)(f"/jobs/{uuid4()}")).status_code == 404


@pytest.mark.parametrize("path", ["/jobs/not-a-uuid", "/jobs?limit=0", "/jobs?status=unknown"])
async def test_invalid_route_parameters(client, path):
    assert (await client.get(path)).status_code == 422


async def test_cancel_running_job_through_api(tmp_path):
    started = asyncio.Event()

    async def slow(request):
        started.set()
        await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as http:
        service = JobService(
            JobRepository(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}"), client=http
        )
        app = create_app(service)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client,
        ):
            job_id = (
                await client.post(
                    "/jobs",
                    json={
                        "job_type": "http_fetch",
                        "payload": {"url": "https://example.com"},
                    },
                )
            ).json()["id"]
            await asyncio.wait_for(started.wait(), 2)
            result = await client.delete(f"/jobs/{job_id}")
            assert result.status_code == 200
            assert result.json()["status"] == "cancelled"
            assert (await client.get("/metrics")).json()["cancelled"] == 1


async def test_lifespan_stops_workers(tmp_path):
    service = JobService(JobRepository(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}"))
    app = create_app(service)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        assert (await client.get("/health")).json()["status"] == "ok"
    assert not service.running
    assert all(worker["state"] == "stopped" for worker in service.workers())
