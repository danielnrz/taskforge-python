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
    assert any("Cannot reach the backend" in error.value for error in app.error)
