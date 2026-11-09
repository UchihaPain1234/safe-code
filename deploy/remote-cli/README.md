# Optional native Claude Code on an existing Ubuntu VPS

This fork adds a lightweight deployment beside the upstream Windows desktop tool.
It installs the unmodified official native Claude Code binary for **one person's
own interactive use**. It is not an HTTP API, subscription relay, account pool,
fingerprint spoofing tool, or guarantee against suspension. Existing OCP,
TeamClaude, Sub2API and browser services are not replaced.

The upstream Windows installer is unchanged and does **not** configure this mode.
Do not run its seven-step installer against a shared production VPS merely to
enable this mode. In particular, its system timezone and bound-IP settings do
not implement a SOCKS5 egress.

## Route and boundaries

```text
Windows SSH terminal -> VPS official Claude Code -> loopback CONNECT bridge
                    -> designated authenticated SOCKS5 -> Anthropic
```

TLS remains end to end. No application header rewriting, device spoofing,
credential sharing, or TLS interception is added. SOCKS5 destinations use remote
DNS. The work user and proxy service have scoped IPv4 and IPv6 output rules;
the designated proxy failing does not authorize direct egress. Other server
users, containers, firewall rules and the host timezone are preserved.

The bridge listens only on `127.0.0.1:31289`. It accepts HTTPS CONNECT on port
443, has bounded headers, connections and timeouts, and records no destinations
or payloads. Firewall access to the listener is limited to the work user and root.
The bridge's upstream SOCKS5 transport itself is not encrypted by this software;
application HTTPS traffic remains encrypted. Proxy credentials therefore have
the ordinary transport limitations of the supplied SOCKS5 endpoint.

## Deployment

Supports Ubuntu 22.04 / 24.04 x86_64 with systemd, Python 3, curl, bash, flock,
iptables and ip6tables. Recommended available memory: at least 1 GB for a single
CLI session; larger repositories and extra tools can require more. No extra
XFCE desktop, browser, Node service or CloudCLI is installed.

Create a root-owned **0600** JSON configuration outside the repository:

```json
{
  "proxy_url": "socks5://USER:PASSWORD@PROXY_IP:443",
  "expected_exit": "EXPECTED_PUBLIC_IPV4",
  "authorized_key": "ssh-ed25519 YOUR_PUBLIC_KEY comment"
}
```

Encode reserved characters in the proxy username/password. Never commit this
configuration. `authorized_key` is optional; it grants only the restricted Claude
entry, with a PTY and no SSH forwarding or arbitrary commands. Existing unrelated
users or login keys are not adopted or overwritten.

```sh
sudo python3 deploy/remote-cli/install.py --config /root/private-config.json
```

Review the sources before executing with root privileges. The installer creates
`safeclaude` (no sudo) and `safe-code-proxy` (nologin), installs two dedicated
systemd units and owner-scoped firewall chains, then downloads the official
installer over the designated proxy. It executes that installer as `safeclaude`,
records its SHA256, and pins a byte-identical, root-owned native binary. Existing
production services are not restarted. A failed installation may leave its
dedicated users and units for diagnosis; the login key is installed last.

## Sign in and work

```sh
ssh -t -i /path/to/dedicated-key safeclaude@YOUR_VPS claude-1
```

Finish the official first-run and sign-in prompts yourself. If the terminal does
not open a browser, use its authorization URL in a browser on the same designated
egress. Paste the resulting code into the **same terminal session**. Never copy
refresh tokens into this project or a different gateway. No account is logged in
by the installer, and no real inference is tested without your sign-in.

Use `claude-2` or `claude-3` for separate `CLAUDE_CONFIG_DIR` directories. Only one
interactive session is permitted across these directories; switching labels is
not automatic routing. Keep one browser profile per account if retaining login.
The launcher removes inherited gateway credentials and sets a process-scoped
`America/Los_Angeles` timezone, with `claude-sonnet-5-5` as the model. Account
permission still has to be verified after sign-in. This does not change the VPS system clock or
impersonate Windows; the native CLI still identifies as Linux.

Projects run in `/home/safeclaude/workspace`, not automatically in the Windows
working directory. This mode does not attach local drives or import local code.
It cannot be selected as an Anthropic Messages channel in Sub2API.

## Checks, limits and maintenance

```sh
python3 -m unittest discover -s deploy/remote-cli -p 'test_*.py' -v
sudo systemctl status safe-code-cli-firewall safe-code-cli-proxy
sudo cat /var/lib/safe-code-cli/deployment.json
sudo -u safeclaude /usr/local/bin/safe-code-claude claude-1 --version
```

The launcher's exit-IP check detects an unexpected route before starting Claude.
The firewall is an additional protection if an application ignores proxy
environment variables. Do not flush these chains while a session is running.
Verify the guard after any host firewall reset, reboot, user-ID change or external
firewall manager update. Resource limits are per the dedicated systemd user slice;
short administrator `runuser` checks outside that slice do not have those limits.

The protected proxy JSON is `/etc/safe-code-cli/proxy.json` (root + proxy group,
0640). Authorization is stored by the official CLI in the selected user's config
directory (0700 parent). Preserve these as secrets in backups.

The deployed binary is pinned; do not claim the desktop application's updater
updates it. To update, first close the session, update the user's official native
installation through the configured proxy, and rerun this installer to verify and
copy the binary again. Do not modify the native binary.

To disable this mode, close its Claude sessions, remove the dedicated SSH key,
and stop/disable `safe-code-cli-proxy`. Retain the egress guard until the work
user's sessions are fully terminated. Remove only `SC-CLI-OUT` / `SC-CLI-LOCAL`
hooks and dedicated files when uninstalling; never flush the host firewall.

## Account risk

There is no public controlled evidence giving a suspension percentage for this
project. A fixed IP or a third-party IP score is not an Anthropic approval.
Subscription eligibility, supported locations, account history, compromised
credentials, usage limits and upstream detection still apply. Compare the
official authentication and permitted-use documentation before signing in.
Using an unmodified official client personally avoids several protocol-conversion
failure modes of API wrappers; it does not remove these other risks.
