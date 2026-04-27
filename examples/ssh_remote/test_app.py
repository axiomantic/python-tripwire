"""Test SSH deployment using tripwire ssh_mock."""

import tripwire

from .app import deploy_config


def test_deploy_config():
    (tripwire.ssh_mock
        .new_session()
        .expect("connect",      returns=None)
        .expect("open_sftp",    returns=None)
        .expect("sftp_put",     returns=None)
        .expect("exec_command", returns=(None, b"", b""))
        .expect("close",        returns=None))

    with tripwire:
        deploy_config(
            "prod-1.example.com", "deploy",
            "/tmp/app.conf", "/etc/myapp/app.conf",
        )

    tripwire.ssh_mock.assert_connect(
        hostname="prod-1.example.com", port=22,
        username="deploy", auth_method="password",
    )
    tripwire.ssh_mock.assert_open_sftp()
    tripwire.ssh_mock.assert_sftp_put(
        localpath="/tmp/app.conf", remotepath="/etc/myapp/app.conf",
    )
    tripwire.ssh_mock.assert_exec_command(command="systemctl reload myapp")
    tripwire.ssh_mock.assert_close()
