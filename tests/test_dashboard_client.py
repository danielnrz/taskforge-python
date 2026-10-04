import httpx
import pytest

from taskforge_python.dashboard_client import cancel_job, fetch_overview, submit_job


def test_dashboard_reads_backend_over_http():
    def respond(request):
        responses = {
            "/metrics": {"total_jobs": 2},
            "/workers": [{"id": "worker-1"}],
            "/jobs": [{"id": "job-1", "status": "succeeded"}],
        }
        return httpx.Response(200, json=responses[request.url.path])

    with httpx.Client(base_url="http://backend", transport=httpx.MockTransport(respond)) as client:
        overview = fetch_overview(client)
    assert overview["metrics"]["total_jobs"] == 2
    assert overview["jobs"][0]["status"] == "succeeded"
    assert overview["workers"][0]["id"] == "worker-1"


def test_dashboard_submits_and_cancels_over_http():
    import json

    def respond(request):
        if request.method == "POST":
            body = json.loads(request.content)
            assert body == {"job_type": "file_checksum", "payload": {"path": "sample.txt"}}
            return httpx.Response(202, json={"id": "job-1", "status": "queued"})
        assert request.method == "DELETE"
        assert request.url.path == "/jobs/job-1"
        return httpx.Response(200, json={"id": "job-1", "status": "cancelled"})

    with httpx.Client(base_url="http://backend", transport=httpx.MockTransport(respond)) as client:
        job = submit_job(client, "file_checksum", {"path": "sample.txt"})
        assert job["status"] == "queued"
        assert cancel_job(client, job["id"])["status"] == "cancelled"


def test_dashboard_preserves_backend_validation_errors():
    with (
        httpx.Client(
            base_url="http://backend",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(422, json={"detail": "invalid path"})
            ),
        ) as client,
        pytest.raises(httpx.HTTPStatusError),
    ):
        submit_job(client, "file_checksum", {"path": ""})


def test_dashboard_handles_non_json_wake_page():
    with (
        httpx.Client(
            base_url="http://backend",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text="<html>Starting service</html>")
            ),
        ) as client,
        pytest.raises(httpx.DecodingError),
    ):
        fetch_overview(client)
