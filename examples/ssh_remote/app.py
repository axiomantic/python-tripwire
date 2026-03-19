"""Deploy configuration to a remote server via SSH."""

import paramiko


def deploy_config(host, username, local_path, remote_path):
    """Upload a config file and reload the application via SSH."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username=username)
    sftp = client.open_sftp()
    sftp.put(local_path, remote_path)
    client.exec_command("systemctl reload myapp")
    client.close()
