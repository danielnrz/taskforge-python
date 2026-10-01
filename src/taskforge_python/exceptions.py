class TaskForgeError(Exception):
    """Base class for expected application errors."""


class JobNotFoundError(TaskForgeError):
    """The requested job does not exist."""


class RetryableJobError(TaskForgeError):
    """A temporary execution failure worth retrying."""


class PermanentJobError(TaskForgeError):
    """An execution failure that another attempt will not fix."""
