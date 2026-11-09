#!/usr/bin/env python3
"""Optional lightweight Ubuntu deployment, separate from the Windows desktop app.

Run as root with --config pointing at a protected JSON file containing
proxy_url, expected_exit and (optionally) authorized_key. No secrets are logged.
"""
import argparse
import grp
import hashlib
import ipaddress
import json
import os
import pwd
import shutil
import stat
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path('/opt/safe-code-cli')
CONF = Path('/etc/safe-code-cli')
STATE = Path('/var/lib/safe-code-cli')


def run(command, *, timeout=60, env=None):
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, env=env)
    if result.returncode:
        # Do not include the environment, proxy URL, or arbitrary command output.
        raise RuntimeError('Deployment command failed: ' + Path(command[0]).name)
    return result.stdout.strip()


def write(path, content, mode=0o644, group=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.new')
    temp.write_text(content)
    temp.chmod(mode)
    os.chown(temp, 0, grp.getgrnam(group).gr_gid if group else 0)
    temp.replace(path)


def user_exists(name):
    try:
        return pwd.getpwnam(name)
    except KeyError:
        return None


def install(config, source):
    if os.geteuid() != 0:
        raise RuntimeError('Root is required')
    os_release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    if os_release.get('ID', '').strip('"') != 'ubuntu' or os.uname().machine != 'x86_64':
        raise RuntimeError('Only Ubuntu x86_64 is supported')
    if os_release.get('VERSION_ID', '').strip('"') not in ('22.04', '24.04'):
        raise RuntimeError('Only Ubuntu 22.04/24.04 is supported')
    from connect_proxy import proxy_settings
    settings = proxy_settings(config['proxy_url'])
    ipaddress.IPv4Address(config['expected_exit'])
    if config.get('listen_port', 31289) != 31289:
        raise RuntimeError('The launcher requires loopback port 31289')
    for name in ('curl', 'bash', 'flock', 'iptables', 'ip6tables', 'systemctl'):
        if not shutil.which(name):
            raise RuntimeError('Missing required system utility: ' + name)
    previous = json.loads((STATE / 'managed.json').read_text()) if (STATE / 'managed.json').exists() else {}
    for name in ('safeclaude', 'safe-code-proxy'):
        if user_exists(name) and name not in previous:
            raise RuntimeError('Refusing to adopt an existing unrelated user: ' + name)
    if not user_exists('safe-code-proxy'):
        run(['useradd', '--system', '--user-group', '--no-create-home', '--shell', '/usr/sbin/nologin', 'safe-code-proxy'])
    if not user_exists('safeclaude'):
        run(['useradd', '--create-home', '--user-group', '--shell', '/bin/bash', 'safeclaude'])
    user = pwd.getpwnam('safeclaude')
    if user.pw_uid == 0 or user.pw_uid in [0, 10001]:
        raise RuntimeError('Unexpected work user ID')
    STATE.mkdir(mode=0o700, exist_ok=True)
    managed = {name: pwd.getpwnam(name).pw_uid for name in ('safeclaude', 'safe-code-proxy')}
    if previous and previous != managed:
        raise RuntimeError('Managed user identity changed')
    write(STATE / 'managed.json', json.dumps(managed), 0o600)
    ROOT.mkdir(mode=0o755, exist_ok=True)
    CONF.mkdir(mode=0o755, exist_ok=True)
    for name in ('connect_proxy.py', 'firewall.py', 'claude.sh', 'ssh_entry.py'):
        write(ROOT / name, (source / name).read_text(), 0o755)
    write(CONF / 'proxy.json', json.dumps({'proxy_url': config['proxy_url'], 'listen_port': 31289}), 0o640, 'safe-code-proxy')
    write(CONF / 'expected-exit', config['expected_exit'] + '\n')
    write('/etc/systemd/system/safe-code-cli-firewall.service', '''[Unit]
Description=Safe Code dedicated user egress guard
Before=ssh.service safe-code-cli-proxy.service
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /opt/safe-code-cli/firewall.py --config /etc/safe-code-cli/proxy.json
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
''')
    write('/etc/systemd/system/safe-code-cli-proxy.service', '''[Unit]
Description=Safe Code loopback CONNECT to designated SOCKS5
Requires=safe-code-cli-firewall.service
After=network-online.target safe-code-cli-firewall.service
[Service]
User=safe-code-proxy
Group=safe-code-proxy
ExecStart=/usr/bin/python3 /opt/safe-code-cli/connect_proxy.py --config /etc/safe-code-cli/proxy.json
Restart=on-failure
RestartSec=3
NoNewPrivileges=yes
PrivateTmp=yes
ProtectHome=yes
ProtectSystem=strict
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
MemoryMax=96M
TasksMax=32
StandardOutput=null
StandardError=journal
[Install]
WantedBy=multi-user.target
''')
    write('/usr/local/bin/safe-code-claude', (source / 'claude.sh').read_text(), 0o755)
    write('/usr/local/libexec/safe-code-ssh', (source / 'ssh_entry.py').read_text(), 0o755)
    # Changes are scoped to this user's sessions; the host timezone is unchanged.
    write(f'/etc/systemd/system/user-{user.pw_uid}.slice.d/safe-code.conf',
          '[Slice]\nMemoryHigh=768M\nMemoryMax=1100M\nTasksMax=192\nCPUQuota=100%\n')
    home = Path(user.pw_dir)
    home.chmod(0o700)
    for name in ('workspace', '.config', '.config/safe-code-cli', '.config/safe-code-cli/accounts', '.ssh', '.local', '.local/bin'):
        path = home / name
        path.mkdir(mode=0o700, exist_ok=True)
        os.chown(path, user.pw_uid, user.pw_gid)
    run(['bash', '-n', '/usr/local/bin/safe-code-claude'])
    run(['systemctl', 'daemon-reload'])
    run(['systemctl', 'enable', '--now', 'safe-code-cli-firewall.service', 'safe-code-cli-proxy.service'])
    run(['systemctl', 'restart', 'safe-code-cli-proxy.service'])
    import time
    for _ in range(20):
        try:
            run(['curl', '--silent', '--fail', '--max-time', '2', 'http://127.0.0.1:31289/health'])
            break
        except RuntimeError:
            time.sleep(0.25)
    else:
        raise RuntimeError('Loopback proxy failed to start')
    env = {'HOME': user.pw_dir, 'USER': user.pw_name, 'PATH': '/usr/local/bin:/usr/bin:/bin',
           'HTTP_PROXY': 'http://127.0.0.1:31289', 'HTTPS_PROXY': 'http://127.0.0.1:31289',
           'NO_PROXY': 'localhost,127.0.0.1,::1', 'TZ': 'America/Los_Angeles', 'LANG': 'C.UTF-8'}
    def as_user(command, timeout=60):
        return run(['runuser', '-u', 'safeclaude', '--', 'env', '-i', *[k+'='+v for k,v in env.items()], *command], timeout=timeout)
    exit_ip = as_user(['curl', '--silent', '--fail', '--connect-timeout', '10', '--max-time', '25', 'https://api.ipify.org'])
    if exit_ip != config['expected_exit']:
        raise RuntimeError('Proxy exit does not match expected_exit')
    binary = home / '.local/bin/claude'
    if not binary.exists():
        installer = STATE / 'install-claude.sh'
        script = as_user(['curl', '--silent', '--fail', '--location', '--connect-timeout', '10', '--max-time', '60', 'https://claude.ai/install.sh'], 70)
        write(installer, script + '\n', 0o644)
        write(STATE / 'installer.sha256', hashlib.sha256(installer.read_bytes()).hexdigest() + '\n', 0o600)
        # Installer source is public but its directory is private. Pass a copy on stdin,
        # executing as the non-sudo user; never run the upstream installer as root.
        command = ['runuser', '-u', 'safeclaude', '--', 'env', '-i', *[k+'='+v for k,v in env.items()], 'bash', '-s', '--', 'stable']
        log = STATE / 'native-install.log'
        with log.open('w') as output:
            result = subprocess.run(command, input=script, text=True, stdout=output, stderr=subprocess.STDOUT, timeout=480)
        log.chmod(0o600)
        if result.returncode:
            raise RuntimeError('Official installer failed; review the protected installation log')
    version = as_user([str(binary), '--version'])
    # Root-owned pin: the launcher does not execute an arbitrary user-replaced path.
    native = Path('/usr/local/libexec/safe-code-native-claude')
    target = binary.resolve(strict=True)
    if not target.is_relative_to(home) or not target.is_file():
        raise RuntimeError('Unexpected native binary location')
    shutil.copyfile(target, native)
    os.chown(native, 0, 0)
    native.chmod(0o755)
    # Official native binary is kept byte-for-byte; record its digest.
    digest = hashlib.sha256(native.read_bytes()).hexdigest()
    authorized = config.get('authorized_key', '').strip()
    if authorized:
        if not authorized.startswith('ssh-ed25519 ') or '\n' in authorized:
            raise RuntimeError('Only one SSH ed25519 public key is accepted')
        key_file = home / '.ssh/authorized_keys'
        line = 'restrict,pty,command="/usr/local/libexec/safe-code-ssh" ' + authorized + '\n'
        if key_file.exists() and key_file.read_text() != line:
            raise RuntimeError('Existing login key differs; refusing to overwrite it')
        write(key_file, line, 0o600)
        os.chown(key_file, user.pw_uid, user.pw_gid)
    result = {'version': version, 'binary_sha256': digest, 'exit_ip': exit_ip,
              'work_user': 'safeclaude', 'public_ports_added': [], 'native_only': True}
    write(STATE / 'deployment.json', json.dumps(result, indent=2), 0o600)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    path = Path(args.config)
    mode = path.stat()
    if mode.st_uid != 0 or stat.S_IMODE(mode.st_mode) & 0o077:
        raise SystemExit('Input configuration must be root-owned with mode 0600')
    print(json.dumps(install(json.loads(path.read_text()), Path(__file__).resolve().parent)))
