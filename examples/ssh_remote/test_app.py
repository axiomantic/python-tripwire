"""Test SSH deployment using bigfoot ssh_mock."""

import bigfoot

from .app import deploy_config


def test_deploy_config():
    (bigfoot.ssh_mock
        .new_session()
        .expect("connect",      returns=None)
        .expect("open_sftp",    returns=None)
        .expect("sftp_put",     returns=None)
        .expect("exec_command", returns=(None, b"", b""))
        .expect("close",        returns=None))

    with bigfoot:
        deploy_config(
            "prod-1.example.com", "deploy",
            "/tmp/app.conf", "/etc/myapp/app.conf",
        )

    bigfoot.ssh_mock.assert_connect(
        hostname="prod-1.example.com", port=22,
        username="deploy", auth_method="password",
    )
    bigfoot.ssh_mock.assert_open_sftp()
    bigfoot.ssh_mock.assert_sftp_put(
        localpath="/tmp/app.conf", remotepath="/etc/myapp/app.conf",
    )
    bigfoot.ssh_mock.assert_exec_command(command="systemctl reload myapp")
    bigfoot.ssh_mock.assert_close()
