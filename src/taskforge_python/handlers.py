import hashlib
from pathlib import Path


class FileChecksumHandler:
    def execute(self, path: str) -> str:
        file_path = Path(path)

        sha256 = hashlib.sha256()

        with file_path.open("rb") as file:
            for chunk in iter(lambda: file.read(8192), b""):
                sha256.update(chunk)

        return sha256.hexdigest()
