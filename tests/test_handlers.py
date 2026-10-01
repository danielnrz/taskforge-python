from taskforge_python.handlers import FileChecksumHandler


def test_file_checksum_handler(tmp_path) -> None:
    file_path = tmp_path / "example.txt"
    file_path.write_text("Hello TaskForge")

    handler = FileChecksumHandler()
    checksum = handler.execute(str(file_path))

    assert checksum == "45f7672365cb146d7293faf0bb7b81ee397978ce388d6f6edf15cf5e004b4d99"
