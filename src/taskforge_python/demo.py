"""Small sample inputs and HTTP targets for the disposable hosted demo."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from taskforge_python.models import JobType
from taskforge_python.service import JobService

router = APIRouter(prefix="/demo", tags=["Demo"])


@router.get("/hello")
async def hello() -> dict[str, str]:
    return {"message": "Hello TaskForge"}


@router.get("/download", response_class=PlainTextResponse)
async def download() -> str:
    return "Hello TaskForge\n"


def prepare_files() -> None:
    directory = Path("data/demo")
    directory.mkdir(parents=True, exist_ok=True)
    samples = {
        "sample.txt": "Hello TaskForge\n",
        "scores.csv": "name,score,hours\nAlice,82,5\nBob,94,7\nCara,88,6\n",
        "invalid.csv": "name,score\nAlice\n",
    }
    for filename, contents in samples.items():
        path = directory / filename
        if not path.exists():
            path.write_text(contents, encoding="utf-8")


async def seed_jobs(service: JobService) -> None:
    """Populate only an empty database; keep existing execution history on restart."""
    if await service.list_jobs(limit=1):
        return
    await service.submit(JobType.FILE_CHECKSUM, {"path": "data/demo/sample.txt"})
    await service.submit(JobType.CSV_SUMMARY, {"path": "data/demo/scores.csv"})
    await service.submit(JobType.CSV_SUMMARY, {"path": "data/demo/invalid.csv"})
