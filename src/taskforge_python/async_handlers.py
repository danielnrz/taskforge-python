import asyncio
import csv
import math
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from taskforge_python.exceptions import PermanentJobError, RetryableJobError
from taskforge_python.handlers import FileChecksumHandler
from taskforge_python.models import Job, JobType
from taskforge_python.payloads import validate_payload


async def run_blocking[T](function: Callable[..., T], *args: Any) -> T:
    """Wait for the thread before releasing resources, even on cancellation."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        finally:
            raise


def summarize_csv(path: str) -> dict[str, object]:
    with Path(path).open(newline="", encoding="utf-8-sig") as file:
        reader = csv.reader(file, strict=True)
        headers = next(reader, [])
        if not headers or any(not header.strip() for header in headers):
            raise PermanentJobError("CSV requires non-empty column names")
        if len(set(headers)) != len(headers):
            raise PermanentJobError("CSV column names must be unique")
        counts = dict.fromkeys(headers, 0)
        numeric: dict[str, list[float]] = {header: [] for header in headers}
        nonnumeric: set[str] = set()
        rows = 0
        for row in reader:
            if len(row) != len(headers):
                raise PermanentJobError(f"CSV row {rows + 2} has the wrong number of fields")
            rows += 1
            for header, value in zip(headers, row, strict=True):
                if not value.strip():
                    continue
                counts[header] += 1
                try:
                    number = float(value)
                except ValueError:
                    nonnumeric.add(header)
                else:
                    if math.isfinite(number):
                        numeric[header].append(number)
                    else:
                        nonnumeric.add(header)
        statistics = {
            header: {
                "count": len(values),
                "min": min(values),
                "max": max(values),
                "mean": math.fsum(values) / len(values),
            }
            for header, values in numeric.items()
            if values and header not in nonnumeric
        }
        return {"rows": rows, "columns": headers, "non_empty": counts, "numeric": statistics}


class AsyncHandlers:
    """Dispatch jobs using a shared HTTP connection pool."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.checksum = FileChecksumHandler()

    async def execute(self, job: Job) -> Any:
        try:
            payload = validate_payload(job.job_type, job.payload)
            if job.job_type == JobType.FILE_CHECKSUM:
                return await run_blocking(self.checksum.execute, str(payload["path"]))
            if job.job_type == JobType.CSV_SUMMARY:
                return await run_blocking(summarize_csv, str(payload["path"]))
            return await self._http(job, payload)
        except httpx.TransportError as exc:
            raise RetryableJobError(f"{type(exc).__name__}: {exc}") from exc
        except (OSError, ValueError, csv.Error, UnicodeError) as exc:
            raise PermanentJobError(f"{type(exc).__name__}: {exc}") from exc

    @staticmethod
    def _check_status(response: httpx.Response) -> None:
        if response.status_code == 429 or response.status_code in {500, 502, 503, 504}:
            raise RetryableJobError(f"HTTP {response.status_code} from {response.url}")
        if not 200 <= response.status_code < 300:
            raise PermanentJobError(f"HTTP {response.status_code} from {response.url}")

    async def _http(self, job: Job, payload: dict[str, object]) -> dict[str, object]:
        limit = int(str(payload["max_bytes"]))
        timeout = float(str(payload["timeout"]))
        async with self.client.stream("GET", str(payload["url"]), timeout=timeout) as response:
            self._check_status(response)
            if job.job_type == JobType.DOWNLOAD_FILE:
                return await self._download(response, str(payload["path"]), limit)
            body = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=65536):
                if len(body) + len(chunk) > limit:
                    raise PermanentJobError(f"Response exceeds {limit} byte limit")
                body.extend(chunk)
            return {
                "url": str(response.url),
                "status_code": response.status_code,
                "content_type": response.headers.get("content-type", ""),
                "bytes": len(body),
                "body": body.decode(response.encoding or "utf-8", errors="replace"),
            }

    async def _download(
        self,
        response: httpx.Response,
        path: str,
        limit: int,
    ) -> dict[str, object]:
        destination = Path(path).resolve()
        if await run_blocking(destination.exists):
            raise PermanentJobError("Download destination already exists")
        # A temporary file in the same directory allows atomic publication.
        file = await run_blocking(
            tempfile.NamedTemporaryFile,
            "wb",
            -1,
            None,
            None,
            ".part",
            "taskforge-",
            str(destination.parent),
            False,
        )
        size = 0
        try:
            async for chunk in response.aiter_bytes(chunk_size=65536):
                size += len(chunk)
                if size > limit:
                    raise PermanentJobError(f"Download exceeds {limit} byte limit")
                await run_blocking(file.write, chunk)
            await run_blocking(file.close)
            # link() fails if another job has already created the destination.
            await run_blocking(os.link, file.name, destination)
            return {"path": str(destination), "bytes": size, "status_code": response.status_code}
        finally:
            await run_blocking(file.close)
            await run_blocking(Path(file.name).unlink, True)
