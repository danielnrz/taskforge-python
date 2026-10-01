"""TaskForge job processing platform."""


def main() -> None:
    import uvicorn

    uvicorn.run("taskforge_python.api:create_app", factory=True, host="127.0.0.1", port=8000)
