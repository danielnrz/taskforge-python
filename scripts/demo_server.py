"""Deterministic local HTTP targets for the portfolio demo."""

import asyncio

from fastapi import FastAPI, Response

app = FastAPI(title="TaskForge demo targets")
attempts: dict[str, int] = {}


@app.get("/hello")
async def hello() -> dict[str, str]:
    return {"message": "Hello from the local demo"}


@app.get("/download")
async def download() -> Response:
    return Response(b"TaskForge local download\n", media_type="text/plain")


@app.get("/flaky")
async def flaky(key: str = "demo") -> Response:
    attempts[key] = attempts.get(key, 0) + 1
    if attempts[key] == 1:
        return Response("Please retry", status_code=503)
    return Response("Recovered after a transient error", media_type="text/plain")


@app.get("/slow")
async def slow() -> dict[str, str]:
    await asyncio.sleep(4)
    return {"message": "Slow request completed"}


@app.get("/missing")
async def missing() -> Response:
    return Response("No such resource", status_code=404)
