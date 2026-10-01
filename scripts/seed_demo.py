"""Populate the dashboard using only local files and HTTP endpoints."""

import argparse
import logging
import time
from pathlib import Path
from uuid import uuid4

import httpx

from taskforge_python.dashboard_client import cancel_job, submit_job


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--targets", default="http://127.0.0.1:8001")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    directory = Path("data/demo").resolve()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "sample.txt").write_text("Hello TaskForge\n")
    (directory / "scores.csv").write_text("name,score,hours\nAlice,82,5\nBob,94,7\nCara,88,6\n")
    (directory / "invalid.csv").write_text("name,score\nAlice\n")
    batch = uuid4().hex[:8]
    with httpx.Client(base_url=args.api, timeout=10) as client:
        client.get("/health").raise_for_status()
        with httpx.Client(timeout=10) as targets:
            targets.get(f"{args.targets}/hello").raise_for_status()
        jobs: list[tuple[str, dict[str, object]]] = [
            ("http_fetch", {"url": f"{args.targets}/hello"}),
            (
                "download_file",
                {
                    "url": f"{args.targets}/download",
                    "path": str(directory / f"download-{batch}.txt"),
                },
            ),
            ("file_checksum", {"path": str(directory / "sample.txt")}),
            ("csv_summary", {"path": str(directory / "scores.csv")}),
            ("http_fetch", {"url": f"{args.targets}/flaky?key={batch}"}),
            ("http_fetch", {"url": f"{args.targets}/missing"}),
            ("csv_summary", {"path": str(directory / "invalid.csv")}),
        ]
        for job_type, payload in jobs:
            job = submit_job(client, job_type, payload)
            logging.info("Queued %s %s", job_type, job["id"][:8])
        cancelled = submit_job(client, "http_fetch", {"url": f"{args.targets}/slow"})
        time.sleep(0.25)
        cancel_job(client, cancelled["id"])
        for _ in range(4):
            submit_job(client, "http_fetch", {"url": f"{args.targets}/slow"})
        logging.info("Demo queued. Open http://127.0.0.1:8501 to watch the workers.")


if __name__ == "__main__":
    main()
