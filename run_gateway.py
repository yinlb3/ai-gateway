"""
Start the LiteLLM gateway with the environment it needs.

Run through run_gateway.bat, which only locates the interpreter and this
file. Keeping the setup in Python rather than in the batch file is
deliberate: cmd.exe reads a .bat as ANSI, so a non-ASCII comment turns
into mojibake and the words on it get executed, and variables set there
were observed not reaching the Prisma client.

Three variables matter, and none of them has a config.yaml key:

    DATABASE_URL                   the Prisma client is built as
                                   Prisma(), which takes no database_url
                                   argument and reads the process
                                   environment instead. The database_url
                                   in config.yaml only decides whether a
                                   database is used at all.
    LITELLM_LOCAL_MODEL_COST_MAP   skip fetching the built-in price
                                   table from GitHub, which times out
                                   here three times before falling back.
    DISABLE_SCHEMA_UPDATE          LiteLLM's baseline migration already
                                   carries every column and the 166
                                   incremental ones add them again, so a
                                   fresh database always resolves
                                   "already exists" for minutes.

Usage:

    python run_gateway.py
    python run_gateway.py --port 4001
"""

# -*- coding: utf-8 -*-
# ==========================================================================
# Founded: 2026-09-22
# Modified: 2026-09-22
# @author yinlb, deepseek-flash
# ==========================================================================

import os
import sys
from pathlib import Path

# Values that must exist before litellm is imported.
#
# DATABASE_URL is deliberately absent: it only feeds the Prisma client,
# which is not needed to forward requests and record them. Setting it
# unconditionally would make litellm enable the database even when
# config.yaml does not ask for one. Add it here when the Prisma problem
# in handoff.md is solved.
DEFAULT_ENV = {
    'PYTHONUTF8': '1',
    'DISABLE_SCHEMA_UPDATE': 'True',
    'LITELLM_LOCAL_MODEL_COST_MAP': 'True',
}

# Set this to a connection string to enable the database features, or
# leave it empty to run without one.
OPTIONAL_ENV = {
    'DATABASE_URL': '',
}

DEFAULT_PORT = '4000'
CONFIG_PATH = 'config/config.yaml'


def prepare_environment() -> None:
    """
    Put the required values into the process environment.

    An existing value is kept, so a caller can point the gateway at
    another database without editing this file. A variable listed with an
    empty value is removed instead, which is how the database is turned
    off: litellm enables it whenever DATABASE_URL is present, whatever
    config.yaml says.

    Returns:
        None.
    """
    for name, value in DEFAULT_ENV.items():
        if not os.environ.get(name):
            os.environ[name] = value

    for name, value in OPTIONAL_ENV.items():
        if value:
            os.environ.setdefault(name, value)
        else:
            os.environ.pop(name, None)


def main(argv: list) -> int:
    """
    Start the proxy in this process.

    Args:
        argv (list): Command line arguments, --port being the useful one.

    Returns:
        int: 0 when the server stops cleanly.
    """
    prepare_environment()

    port = DEFAULT_PORT
    if '--port' in argv:
        port = argv[argv.index('--port') + 1]

    if not Path(CONFIG_PATH).is_file():
        print(f'ERROR  config not found: {CONFIG_PATH}')
        return 1

    print(f'DATABASE_URL = {os.environ.get("DATABASE_URL") or "(none)"}')
    print(f'starting litellm on port {port}')

    # litellm parses the command line itself, so the arguments are set
    # here rather than passed through.
    sys.argv = ['litellm', '--config', CONFIG_PATH, '--port', port]

    from litellm import run_server

    run_server()
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
