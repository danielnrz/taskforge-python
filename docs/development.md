# V1 development plan

TaskForge is a local, single-process job platform for demonstrating readable
Python engineering. Keep the existing dataclass lifecycle and synchronous
checksum runner. Extend through composition, using free local tooling.

## Milestones

1. **Handlers and quality tools**: add validated payloads, asynchronous HTTP
   fetch/download handlers, a CSV summary, meaningful execution exceptions,
   pytest-asyncio, ruff and mypy. Test mocked status codes, network timeout,
   malformed CSV, filesystem failures and bounded response sizes.
2. **Persistence and workers**: add a SQLAlchemy SQLite repository, a simple
   retry policy, asyncio queue and worker coroutines coordinated by JobService.
   Test retries, attempt limits, concurrency, cancellation and restart recovery.
3. **API**: use Pydantic at the boundary and thin FastAPI routes. Lifespan owns
   startup/shutdown. Test validation, job endpoints, workers, health and metrics.
4. **Dashboard and demo**: Streamlit calls the API over HTTP. Show status cards,
   workers, recent jobs, durations, retries and failures; provide submission and
   cancellation controls and a local deterministic demo.
5. **Packaging and verification**: add CI, Docker and setup documentation.
   Run the full test suite, lint/type checks and local backend/dashboard smoke
   tests. Build and run Docker when available, inspect the final diff and push.

For each milestone, write behavior tests first, observe failure, implement,
run targeted and regression tests, then commit a coherent change.

## Decisions

- SQLite is durable storage; asyncio.Queue is in-memory. Restore queued jobs;
  interrupted running/retrying jobs become failed to avoid repeating file writes.
- Cancellation interrupts network requests and backoff. Blocking local work
  runs in a thread and must finish before its cancellation completes.
- Three attempts with 0.5, 1.0 second backoff by default. Retry connection errors,
  timeouts, 429 and 500/502/503/504. Other failures are permanent.
- The app is for a trusted local user, binds to loopback, and uses one backend
  process. No authentication or distributed execution in V1.
- HTTP fetches and downloads have byte limits. Downloads never replace an
  existing file and use temporary files cleaned up on failure/cancellation.
- Keep the original synchronous JobRunner as a small compatibility example;
  the service uses a separate AsyncJobRunner with the same lifecycle model.
