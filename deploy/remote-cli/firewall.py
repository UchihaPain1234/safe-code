#!/usr/bin/env python3
"""Manage only dedicated users' OUTPUT traffic. No host-wide route changes."""
import argparse
import ipaddress
import json
import pwd
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

def apply(config):
    user_uid = str(pwd.getpwnam('safeclaude').pw_uid)
    proxy_uid = str(pwd.getpwnam('safe-code-proxy').pw_uid)
    endpoint = urlsplit(config['proxy_url'])
    address = ipaddress.ip_address(endpoint.hostname)
    port = str(config.get('listen_port', 31289))
    for tool, version in (('/usr/sbin/iptables', 4), ('/usr/sbin/ip6tables', 6)):
        def run(*args, check=True):
            return subprocess.run([tool, '-w', '10', *args], check=check, capture_output=True)
        chain = 'SC-CLI-OUT'
        run('-N', chain, check=False)
        # A terminal REJECT stays in place while the desired rules are rebuilt.
        # The service is installed before the work user receives a login key.
        if run('-C', chain, '-j', 'REJECT', check=False).returncode:
            run('-A', chain, '-j', 'REJECT')
        for uid in (user_uid, proxy_uid):
            rule = ('-m', 'owner', '--uid-owner', uid, '-j', chain)
            if run('-C', 'OUTPUT', *rule, check=False).returncode:
                run('-I', 'OUTPUT', '1', *rule)
        # Atomic restore changes only this dedicated chain; never flush tables.
        loopback = '127.0.0.1/32' if version == 4 else '::1/128'
        rules = [f'-A {chain} -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT',
                 f'-A {chain} -m owner --uid-owner {user_uid} -d {loopback} -p tcp --dport {port} -j ACCEPT']
        if address.version == version:
            rules.append(f'-A {chain} -m owner --uid-owner {proxy_uid} -d {address} -p tcp --dport {endpoint.port} -j ACCEPT')
        rules.append(f'-A {chain} -j REJECT')
        # iptables-restore --noflush preserves all unrelated chains and rules.
        text = '*filter\n-F '+chain+'\n'+'\n'.join(rules)+'\nCOMMIT\n'
        subprocess.run([tool+'-restore', '-w', '10', '--noflush'], input=text.encode(), check=True, capture_output=True)
        # Only the work user and root may connect to the loopback listener.
        gate = 'SC-CLI-LOCAL'
        run('-N', gate, check=False)
        text = ('*filter\n-F '+gate+'\n'
                f'-A {gate} -m owner --uid-owner 0 -j ACCEPT\n'
                f'-A {gate} -m owner --uid-owner {user_uid} -j ACCEPT\n'
                f'-A {gate} -j REJECT\nCOMMIT\n')
        subprocess.run([tool+'-restore', '-w', '10', '--noflush'], input=text.encode(), check=True, capture_output=True)
        rule = ('-d', loopback, '-p', 'tcp', '--dport', port, '-j', gate)
        if run('-C', 'OUTPUT', *rule, check=False).returncode:
            run('-I', 'OUTPUT', '1', *rule)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    apply(json.loads(Path(args.config).read_text()))
