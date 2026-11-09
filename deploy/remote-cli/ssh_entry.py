#!/usr/bin/env python3
"""Restricted SSH entry: a named Claude account, never an arbitrary shell."""
import os
import re
import sys
from pathlib import Path

account = os.environ.get('SSH_ORIGINAL_COMMAND', '').strip() or 'claude-1'
if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,31}', account):
    sys.exit('Use one account label, for example: ssh safeclaude@server claude-1')
os.chdir(Path.home() / 'workspace')
os.execv('/usr/local/bin/safe-code-claude', ['safe-code-claude', account])
