"""Noninteractive Cursor authentication: memory only, no macOS keychain helper."""
import os,re,sys
from pathlib import Path

SECURITY_HELPER_BLOCK='(version 1) (allow default) (deny process-exec (literal "/usr/bin/security"))'


def apply_cursor_memory_auth(env):
    if sys.platform=='darwin':
        import pwd
        env['HOME']=pwd.getpwuid(os.getuid()).pw_dir
    env['AGENT_CLI_CREDENTIAL_STORE']='memory'
    env['DIRENV_DISABLE']='1'
    return env


def keychain_safe_cursor_command(binary,args):
    binary=Path(binary).resolve();entry=binary.parent/'index.js'
    if not entry.exists():raise RuntimeError('Cannot verify native Cursor memory credential support; refusing interactive authentication')
    source=entry.read_text(errors='replace')
    if not re.search(r'AGENT_CLI_CREDENTIAL_STORE.{0,180}["\x27]memory["\x27]',source):
        raise RuntimeError('Cursor memory credential storage is unverified; refusing to risk keychain dialogs')
    command=[str(binary),*args]
    if sys.platform=='darwin':
        sandbox=Path('/usr/bin/sandbox-exec')
        if not sandbox.exists():raise RuntimeError('macOS keychain-helper guard is unavailable; Cursor remains stopped')
        command=[str(sandbox),'-p',SECURITY_HELPER_BLOCK,*command]
    return command
