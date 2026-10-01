from taskforge_python.handlers import FileChecksumHandler
from taskforge_python.models import Job, JobType


class JobRunner:
    def __init__(self) -> None:
        self.checksum_handler = FileChecksumHandler()

    def run(self, job: Job, worker_id: str) -> None:
        job.mark_running(worker_id)

        try:
            if job.job_type == JobType.FILE_CHECKSUM:
                path = str(job.payload["path"])
                result = self.checksum_handler.execute(path)
            else:
                raise ValueError(f"Unsupported job type: {job.job_type}")

            job.mark_succeeded(result)

        except Exception as exc:
            job.mark_failed(str(exc))
