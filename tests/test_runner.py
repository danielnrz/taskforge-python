from taskforge_python.models import Job, JobStatus, JobType
from taskforge_python.runner import JobRunner


def test_runner_completes_checksum_job(tmp_path) -> None:
    file_path = tmp_path / "example.txt"
    file_path.write_text("Hello TaskForge")

    job = Job(JobType.FILE_CHECKSUM, {"path": str(file_path)})
    runner = JobRunner()

    runner.run(job, "worker-1")

    assert job.status == JobStatus.SUCCEEDED
    assert job.result is not None
    assert job.worker_id == "worker-1"
    assert job.attempt == 1


def test_runner_marks_missing_file_as_failed(tmp_path) -> None:
    missing_file = tmp_path / "missing.txt"

    job = Job(JobType.FILE_CHECKSUM, {"path": str(missing_file)})
    runner = JobRunner()

    runner.run(job, "worker-1")

    assert job.status == JobStatus.FAILED
    assert job.error is not None
    assert job.result is None


def test_runner_marks_unsupported_job_as_failed() -> None:
    job = Job(JobType.HTTP_FETCH, {"url": "https://example.com"})
    runner = JobRunner()

    runner.run(job, "worker-1")

    assert job.status == JobStatus.FAILED
    assert job.error == "Unsupported job type: http_fetch"
