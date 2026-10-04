from typing import Any, cast

import httpx


def _json(response: httpx.Response) -> Any:
    response.raise_for_status()
    try:
        return response.json()
    except ValueError as exc:
        raise httpx.DecodingError(
            "Backend returned non-JSON content", request=response.request
        ) from exc


def fetch_overview(client: httpx.Client) -> dict[str, Any]:
    return {
        "metrics": _json(client.get("/metrics")),
        "workers": _json(client.get("/workers")),
        "jobs": _json(client.get("/jobs", params={"limit": 200})),
    }


def submit_job(client: httpx.Client, job_type: str, payload: dict[str, object]) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        _json(
            client.post(
                "/jobs",
                json={
                    "job_type": job_type,
                    "payload": payload,
                },
            )
        ),
    )


def cancel_job(client: httpx.Client, job_id: str) -> dict[str, Any]:
    return cast(dict[str, Any], _json(client.delete(f"/jobs/{job_id}")))
