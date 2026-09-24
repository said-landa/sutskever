"""Invoke the official CLI using the existing credential without printing it."""
import os
from pathlib import Path
import subprocess
import sys
import tomllib

def main():
    key = os.environ.get('RUNPOD_API_KEY')
    if not key:
        config = tomllib.loads((Path.home() / '.runpod/config.toml').read_text())
        key = config['default']['api_key']
    return subprocess.call(['/private/tmp/vision-runpodctl', *sys.argv[1:]],
                           env={**os.environ, 'RUNPOD_API_KEY': key})

if __name__ == '__main__':
    sys.exit(main())
