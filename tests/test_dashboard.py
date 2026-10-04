import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from streamlit.testing.v1 import AppTest

from taskforge_python.api import create_app
from taskforge_python.repository import JobRepository
from taskforge_python.service import JobService


@pytest.fixture
def backend(tmp_path):
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text="dashboard request succeeded")
        )
    )
    service = JobService(
        JobRepository(f"sqlite+aiosqlite:///{tmp_path / 'dashboard.db'}"), client=http
    )
    app = create_app(service)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    address = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("Test backend did not start")
        time.sleep(0.01)
    try:
        yield address
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        assert not thread.is_alive()


def test_dashboard_displays_and_submits_to_live_api(backend, monkeypatch):
    monkeypatch.setenv("TASKFORGE_API_URL", backend)
    app = AppTest.from_file(str(Path("dashboard/app.py").resolve()), default_timeout=15).run()
    assert not app.exception
    assert not app.error
    assert next(metric for metric in app.metric if metric.label == "Total jobs").value == "0"
    next(button for button in app.button if button.label == "Queue job").click().run()
    assert not app.exception
    assert app.success
    with httpx.Client(base_url=backend) as client:
        jobs = client.get("/jobs").json()
        assert len(jobs) == 1
        assert jobs[0]["job_type"] == "http_fetch"
    app.run()
    assert next(metric for metric in app.metric if metric.label == "Total jobs").value == "1"
    assert not app.error


def test_dashboard_handles_unavailable_backend(monkeypatch):
    monkeypatch.setenv("TASKFORGE_API_URL", "http://127.0.0.1:1")
    app = AppTest.from_file(str(Path("dashboard/app.py").resolve()), default_timeout=15).run()
    assert not app.exception
    assert not app.error
    assert any("Backend is starting" in item.value for item in app.info)
    assert any(button.label == "Retry connection" for button in app.button)


def test_dashboard_shows_empty_status_filter(backend, monkeypatch):
    monkeypatch.setenv("TASKFORGE_API_URL", backend)
    with httpx.Client(base_url=backend) as client:
        response = client.post(
            "/jobs",
            json={
                "job_type": "http_fetch",
                "payload": {"url": "https://example.com"},
            },
        )
        response.raise_for_status()
    app = AppTest.from_file(str(Path("dashboard/app.py").resolve()), default_timeout=15).run()
    app.segmented_control[0].set_value("Failed").run()
    assert not app.exception
    assert any("No jobs match this status" in item.value for item in app.info)


def test_dashboard_reads_root_level_secret(backend, monkeypatch):
    monkeypatch.delenv("TASKFORGE_API_URL", raising=False)
    app = AppTest.from_file(str(Path("dashboard/app.py").resolve()), default_timeout=15)
    app.secrets["TASKFORGE_API_URL"] = backend
    app.run()
    assert not app.exception
    assert not app.error
    assert app.text_input[0].value == backend
    assert next(metric for metric in app.metric if metric.label == "Total jobs").value == "0"


@pytest.mark.parametrize("status", [200, 502, 503, 504])
def test_dashboard_handles_wake_responses(status, monkeypatch):
    from taskforge_python import dashboard_client

    original = dashboard_client.fetch_overview

    def wake_response(client):
        with httpx.Client(
            base_url="http://starting",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(status, text="<html>Starting</html>")
            ),
        ) as waking:
            return original(waking)

    monkeypatch.setattr(dashboard_client, "fetch_overview", wake_response)
    app = AppTest.from_file(str(Path("dashboard/app.py").resolve()), default_timeout=15).run()
    assert not app.exception
    assert not app.error
    assert any("Backend is starting" in item.value for item in app.info)
    next(button for button in app.button if button.label == "Retry connection").click().run()
    assert not app.exception
