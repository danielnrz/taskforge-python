import asyncio
from pathlib import Path

import httpx

from taskforge_python.api import create_app
from taskforge_python.repository import JobRepository
from taskforge_python.service import JobService


async def test_demo_restores_files_and_seeds_only_empty_database(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TASKFORGE_DEMO_MODE", "true")
    url = f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}"

    async def start_demo():
        service = JobService(JobRepository(url))
        app = create_app(service)
        async with app.router.lifespan_context(app):
            async with asyncio.timeout(3):
                while (await service.metrics())["queued"] or (await service.metrics())["running"]:
                    await asyncio.sleep(0.01)
            jobs = await service.list_jobs()
            assert len(jobs) == 3
            assert (await service.metrics())["succeeded"] == 2
            assert (await service.metrics())["failed"] == 1
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://demo"
            ) as client:
                assert (await client.get("/demo/hello")).status_code == 200
                assert (await client.get("/demo/download")).text == "Hello TaskForge\n"
            return {job.id for job in jobs}

    first_ids = await start_demo()
    sample = Path("data/demo/sample.txt")
    sample.write_text("Keep this file\n")
    Path("data/demo/scores.csv").unlink()
    assert await start_demo() == first_ids
    assert sample.read_text() == "Keep this file\n"
    assert Path("data/demo/scores.csv").exists()
    (tmp_path / "jobs.db").unlink()
    assert await start_demo() != first_ids


async def test_demo_disabled_preserves_normal_startup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TASKFORGE_DEMO_MODE", raising=False)
    service = JobService(JobRepository(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}"))
    app = create_app(service)
    async with app.router.lifespan_context(app):
        assert await service.list_jobs() == []
        assert not Path("data/demo").exists()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://demo"
        ) as client:
            assert (await client.get("/demo/hello")).status_code == 404
