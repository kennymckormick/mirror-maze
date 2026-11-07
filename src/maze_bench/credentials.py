"""Load the user-authorized native Cursor credential without shell evaluation."""
import os,re,shlex
from pathlib import Path


def cursor_api_key():
    value=os.environ.get('CURSOR_API_KEY','').strip()
    if value:return value
    alias=Path.home()/'.alias'
    if alias.exists():
        for line in reversed(alias.read_text().splitlines()):
            match=re.match(r'^\s*(?:export\s+)?CURSOR_API_KEY\s*=\s*(.+?)\s*$',line)
            if not match:continue
            raw=match.group(1)
            if '$'in raw or '`'in raw:raise RuntimeError('CURSOR_API_KEY must be exported or use a literal assignment in ~/.alias')
            values=shlex.split(raw,comments=True,posix=True)
            if len(values)==1 and values[0]:return values[0]
    raise RuntimeError('CURSOR_API_KEY was not found in the environment or a literal ~/.alias assignment')
