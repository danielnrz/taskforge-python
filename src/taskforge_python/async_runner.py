import asyncio
import logging
from collections.abc import Awaitable, Callable
from time import monotonic

from taskforge_python.async_handlers import AsyncHandlers
from taskforge_python.exceptions import RetryableJobError
from taskforge_python.models import Job, JobStatus
from taskforge_python.retry import RetryPolicy

logger = logging.getLogger(__name__)


class AsyncJobRunner:
    def __init__(self, handlers: AsyncHandlers, retry_policy: RetryPolicy) -> None:
        self.handlers = handlers
        self.retry_policy = retry_policy

    async def run(
        self,
        job: Job,
        worker_id: str,
        save: Callable[[Job], Awaitable[None]],
    ) -> None:
        started = monotonic()
        try:
            while True:
                job.mark_running(worker_id)
                await save(job)
                logger.info(
                    "job=%s worker=%s attempt=%s event=running", job.id, worker_id, job.attempt
                )
                try:
                    result = await self.handlers.execute(job)
                except RetryableJobError as exc:
                    job.error_type = type(exc).__name__
                    if job.attempt >= self.retry_policy.max_attempts:
                        job.mark_failed(str(exc))
                        break
                    job.mark_retrying(str(exc))
                    await save(job)
                    logger.warning(
                        "job=%s worker=%s attempt=%s event=retrying error=%s",
                        job.id,
                        worker_id,
                        job.attempt,
                        exc,
                    )
                    await asyncio.sleep(self.retry_policy.delay(job.attempt))
                except Exception as exc:
                    job.error_type = type(exc).__name__
                    job.mark_failed(str(exc))
                    break
                else:
                    job.mark_succeeded(result)
                    job.error_type = None
                    break
        except asyncio.CancelledError:
            if job.status in {JobStatus.RUNNING, JobStatus.RETRYING, JobStatus.QUEUED}:
                job.mark_cancelled()
        finally:
            await save(job)
            logger.info(
                "job=%s worker=%s attempt=%s event=%s runtime=%.3f error=%s",
                job.id,
                worker_id,
                job.attempt,
                job.status,
                monotonic() - started,
                job.error,
            )
