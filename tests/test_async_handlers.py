import hashlib
from pathlib import Path

import httpx
import pytest

from taskforge_python.async_handlers import AsyncHandlers
from taskforge_python.exceptions import PermanentJobError, RetryableJobError
from taskforge_python.models import Job, JobType
from taskforge_python.payloads import validate_payload


@pytest.mark.parametrize(
    "job_type,payload",
    [
        (JobType.HTTP_FETCH, {"url": "file:///etc/passwd"}),
        (JobType.HTTP_FETCH, {"url": "https://example.com", "timeout": 0}),
        (JobType.HTTP_FETCH, {"url": "https://example.com", "unknown": True}),
        (JobType.DOWNLOAD_FILE, {"url": "https://example.com"}),
        (JobType.FILE_CHECKSUM, {"path": ""}),
        (JobType.CSV_SUMMARY, {"path": 42}),
    ],
)
def test_payload_rejects_invalid_configuration(job_type, payload):
    with pytest.raises(ValueError):
        validate_payload(job_type, payload)


async def test_fetch_returns_metadata_and_body():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, text="Hello", headers={"content-type": "text/plain"}
            )
        )
    ) as client:
        result = await AsyncHandlers(client).execute(
            Job(
                JobType.HTTP_FETCH,
                {
                    "url": "https://example.com",
                },
            )
        )
    assert result["status_code"] == 200
    assert result["body"] == "Hello"
    assert result["bytes"] == 5


@pytest.mark.parametrize(
    "status,error",
    [
        (429, RetryableJobError),
        (500, RetryableJobError),
        (502, RetryableJobError),
        (503, RetryableJobError),
        (504, RetryableJobError),
        (404, PermanentJobError),
        (401, PermanentJobError),
        (301, PermanentJobError),
        (501, PermanentJobError),
    ],
)
async def test_http_failure_classification(status, error):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(status))
    ) as client:
        with pytest.raises(error):
            await AsyncHandlers(client).execute(
                Job(
                    JobType.HTTP_FETCH,
                    {
                        "url": "https://example.com",
                    },
                )
            )


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadTimeout])
async def test_network_failure_is_retryable(error):
    def fail(request):
        raise error("network unavailable", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(RetryableJobError):
            await AsyncHandlers(client).execute(
                Job(
                    JobType.HTTP_FETCH,
                    {
                        "url": "https://example.com",
                    },
                )
            )


async def test_fetch_enforces_size_limit():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"12345"))
    ) as client:
        with pytest.raises(PermanentJobError, match="limit"):
            await AsyncHandlers(client).execute(
                Job(
                    JobType.HTTP_FETCH,
                    {
                        "url": "https://example.com",
                        "max_bytes": 4,
                    },
                )
            )


async def test_download_writes_file_without_overwriting(tmp_path):
    target = tmp_path / "result.txt"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"hello"))
    ) as client:
        handler = AsyncHandlers(client)
        job = Job(JobType.DOWNLOAD_FILE, {"url": "https://example.com", "path": str(target)})
        result = await handler.execute(job)
        assert target.read_bytes() == b"hello"
        assert result["bytes"] == 5
        with pytest.raises(PermanentJobError):
            await handler.execute(job)
    assert list(tmp_path.iterdir()) == [target]


async def test_failed_download_cleans_partial_file(tmp_path):
    target = tmp_path / "result.txt"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"12345"))
    ) as client:
        with pytest.raises(PermanentJobError):
            await AsyncHandlers(client).execute(
                Job(
                    JobType.DOWNLOAD_FILE,
                    {
                        "url": "https://example.com",
                        "path": str(target),
                        "max_bytes": 4,
                    },
                )
            )
    assert list(tmp_path.iterdir()) == []


async def test_local_handlers(tmp_path):
    path = tmp_path / "data.csv"
    path.write_text("name,score\nAlice,2\nBob,4\n")
    async with httpx.AsyncClient() as client:
        handler = AsyncHandlers(client)
        checksum = await handler.execute(Job(JobType.FILE_CHECKSUM, {"path": str(path)}))
        summary = await handler.execute(Job(JobType.CSV_SUMMARY, {"path": str(path)}))
    assert checksum == hashlib.sha256(path.read_bytes()).hexdigest()
    assert summary["rows"] == 2
    assert summary["numeric"]["score"] == {"count": 2, "min": 2.0, "max": 4.0, "mean": 3.0}


@pytest.mark.parametrize("content", ["", "a,a\n1,2\n", "a,b\n1\n", 'a\n"unclosed'])
async def test_malformed_csv_is_permanent(tmp_path, content):
    path = tmp_path / "bad.csv"
    path.write_text(content)
    async with httpx.AsyncClient() as client:
        with pytest.raises(PermanentJobError):
            await AsyncHandlers(client).execute(Job(JobType.CSV_SUMMARY, {"path": str(path)}))


@pytest.mark.parametrize("job_type", [JobType.CSV_SUMMARY, JobType.FILE_CHECKSUM])
async def test_missing_file_is_permanent(tmp_path: Path, job_type):
    async with httpx.AsyncClient() as client:
        with pytest.raises(PermanentJobError):
            await AsyncHandlers(client).execute(Job(job_type, {"path": str(tmp_path / "missing")}))
