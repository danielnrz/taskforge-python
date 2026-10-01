from taskforge_python.models import Job, JobStatus, JobType
import pytest

def test_queued_job_cannot_succeed() -> None:
    job = Job(JobType.HTTP_FETCH, {"url": "https://example.com"})

    with pytest.raises(ValueError):
        job.mark_succeeded()

def test_succeeded_job_cannot_start_again() -> None:
    job = Job(JobType.HTTP_FETCH, {"url": "https://example.com"})
    job.mark_running("worker-1")
    job.mark_succeeded()

    with pytest.raises(ValueError):
        job.mark_running("worker-2")

def test_running_job_can_retry() -> None:
    job = Job(JobType.HTTP_FETCH, {"url": "https://example.com"})
    job.mark_running("worker-1")

    job.mark_retrying("Request timed out")

    assert job.status == JobStatus.RETRYING
    assert job.error == "Request timed out"
    assert job.worker_id is None
    assert job.attempt == 1

def test_queued_job_cannot_fail() -> None:
    job = Job(JobType.HTTP_FETCH, {"url": "https://example.com"})

    with pytest.raises(ValueError):
        job.mark_failed("Something went wrong")

def test_new_job_is_queued() -> None:
    job = Job(JobType.HTTP_FETCH, {"url": "https://example.com"})

    assert job.status == JobStatus.QUEUED
    assert job.attempt == 0


def test_job_can_start() -> None:
    job = Job(JobType.HTTP_FETCH, {"url": "https://example.com"})

    job.mark_running("worker-1")

    assert job.status == JobStatus.RUNNING
    assert job.attempt == 1
    assert job.worker_id == "worker-1"
    assert job.started_at is not None


def test_job_can_succeed() -> None:
    job = Job(JobType.HTTP_FETCH, {"url": "https://example.com"})
    job.mark_running("worker-1")

    job.mark_succeeded({"status_code": 200})

    assert job.status == JobStatus.SUCCEEDED
    assert job.result == {"status_code": 200}
    assert job.completed_at is not None


def test_job_can_fail() -> None:
    job = Job(JobType.CSV_SUMMARY, {"path": "data.csv"})
    job.mark_running("worker-1")

    job.mark_failed("Malformed CSV")

    assert job.status == JobStatus.FAILED
    assert job.error == "Malformed CSV"
    assert job.completed_at is not None


def test_job_can_be_cancelled() -> None:
    job = Job(JobType.DOWNLOAD_FILE, {"url": "https://example.com/file.zip"})

    job.mark_cancelled()

    assert job.status == JobStatus.CANCELLED
    assert job.completed_at is not None

def test_retrying_job_can_start_again() -> None:
    job = Job(JobType.HTTP_FETCH, {"url": "https://example.com"})
    job.mark_running("worker-1")
    job.mark_retrying("Request timed out")

    job.mark_running("worker-2")

    assert job.status == JobStatus.RUNNING
    assert job.attempt == 2
    assert job.worker_id == "worker-2"
    assert job.error is None

def test_succeeded_job_cannot_be_cancelled() -> None:
    job = Job(JobType.HTTP_FETCH, {"url": "https://example.com"})
    job.mark_running("worker-1")
    job.mark_succeeded()

    with pytest.raises(ValueError):
        job.mark_cancelled()

def test_queued_job_cannot_retry() -> None:
    job = Job(JobType.HTTP_FETCH, {"url": "https://example.com"})

    with pytest.raises(ValueError):
        job.mark_retrying("Request timed out")