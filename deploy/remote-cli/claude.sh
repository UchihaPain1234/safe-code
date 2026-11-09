#!/bin/bash
set -euo pipefail
if [[ $(id -un) != safeclaude ]]; then
  echo 'Run this launcher as the dedicated safeclaude user.' >&2
  exit 1
fi
account=${1:-claude-1}
[[ $# -eq 0 ]] || shift
if [[ ! $account =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]{0,31}$ ]]; then
  echo 'Account label must contain 1-32 ASCII letters, digits, _ or -.' >&2
  exit 1
fi
umask 077
mkdir -p "$HOME/.config/safe-code-cli/accounts/$account"
exec 9>"$HOME/.config/safe-code-cli/session.lock"
if ! flock -n 9; then
  echo 'Another remote Claude session is active. Finish it before switching accounts.' >&2
  exit 1
fi
unset ANTHROPIC_BASE_URL ANTHROPIC_AUTH_TOKEN ANTHROPIC_API_KEY CLAUDE_CODE_OAUTH_TOKEN
unset CLAUDE_CODE_USE_BEDROCK CLAUDE_CODE_USE_VERTEX CLAUDE_CODE_USE_FOUNDRY
unset http_proxy https_proxy all_proxy ALL_PROXY no_proxy
export HTTP_PROXY=http://127.0.0.1:31289
export HTTPS_PROXY=$HTTP_PROXY
export NO_PROXY=localhost,127.0.0.1,::1
export TZ=America/Los_Angeles
export ANTHROPIC_MODEL=claude-sonnet-5-5
export CLAUDE_CONFIG_DIR="$HOME/.config/safe-code-cli/accounts/$account"
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
# Fail before opening a session if the designated proxy is down or changed.
exit_ip=$(curl --silent --show-error --fail --connect-timeout 10 --max-time 25 https://api.ipify.org)
expected=$(cat /etc/safe-code-cli/expected-exit)
if [[ $exit_ip != "$expected" ]]; then
  echo 'The configured egress check failed. No Claude session was started.' >&2
  exit 1
fi
if [[ -x /usr/local/libexec/safe-code-native-claude ]]; then
  exec /usr/local/libexec/safe-code-native-claude "$@"
fi
exec "$HOME/.local/bin/claude" "$@"
