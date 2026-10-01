from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from taskforge_python.models import JobType


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FilePayload(Payload):
    path: Annotated[str, Field(min_length=1, max_length=4096)]


class HttpPayload(Payload):
    url: HttpUrl
    timeout: Annotated[float, Field(gt=0, le=120, allow_inf_nan=False)] = 10
    max_bytes: Annotated[int, Field(gt=0, le=10_000_000, strict=True)] = 1_000_000


class DownloadPayload(HttpPayload):
    path: Annotated[str, Field(min_length=1, max_length=4096)]
    max_bytes: Annotated[int, Field(gt=0, le=100_000_000, strict=True)] = 10_000_000


def validate_payload(job_type: JobType, payload: dict[str, object]) -> dict[str, object]:
    models: dict[JobType, type[Payload]] = {
        JobType.HTTP_FETCH: HttpPayload,
        JobType.DOWNLOAD_FILE: DownloadPayload,
        JobType.FILE_CHECKSUM: FilePayload,
        JobType.CSV_SUMMARY: FilePayload,
    }
    return dict(models[job_type].model_validate(payload).model_dump(mode="json"))
