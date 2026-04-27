"""Test file archival using tripwire file_io_mock."""

import tripwire

from .app import archive_and_clean


def test_archive_and_clean():
    tripwire.file_io_mock.mock_operation("makedirs", "/backups/2024", returns=None)
    tripwire.file_io_mock.mock_operation("copytree", "/var/data/reports", returns=None)
    tripwire.file_io_mock.mock_operation(
        "write_text", "/var/log/manifest.txt", returns=None,
    )
    tripwire.file_io_mock.mock_operation("rmtree", "/var/data/reports", returns=None)

    with tripwire:
        archive_and_clean(
            "/var/data/reports", "/backups/2024", "/var/log/manifest.txt",
        )

    tripwire.file_io_mock.assert_makedirs(path="/backups/2024", exist_ok=True)
    tripwire.file_io_mock.assert_copytree(
        src="/var/data/reports", dst="/backups/2024/latest",
    )
    tripwire.file_io_mock.assert_write_text(
        path="/var/log/manifest.txt", data="archived: /var/data/reports",
    )
    tripwire.file_io_mock.assert_rmtree(path="/var/data/reports")
