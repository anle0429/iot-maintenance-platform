#!/usr/bin/env python3
"""Audit each Go mutant with original test JSON; compilation/harness failures never count as test kills."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import os
import subprocess
import time


def main():
    """Modify only a disposable sealed work copy, always restore it and retain every test process outcome."""
    work = Path(os.environ['IOT_MUTATION_WORKDIR']).resolve()
    output = Path(os.environ['IOT_MUTATION_RESULTS']).resolve()
    original = Path(os.environ['MUTATE_ORIGINAL']).resolve()
    changed = Path(os.environ['MUTATE_CHANGED']).resolve()
    if original != work / 'main.go' or not changed.is_file() or not output.is_relative_to(work.parent):
        raise ValueError('Require a disposable work copy and its owned result directory')
    before = original.read_bytes()
    if sha256(before).hexdigest() != os.environ['IOT_MUTATION_SOURCE_SHA256']:
        raise ValueError('The original work file differs from the sealed source')
    mutation = changed.read_bytes(); pin = sha256(mutation).hexdigest()
    output.mkdir(exist_ok=True)
    started = time.monotonic(); native_code = None; reason = 'harness_error'; engine_code = 3
    stdout = b''; stderr = b''
    try:
        original.write_bytes(mutation)
        try:
            result = subprocess.run([os.environ['IOT_GO'], 'test', '-count=1', '-json', '.'],
                cwd=work, capture_output=True, timeout=8)
            native_code = result.returncode; stdout = result.stdout; stderr = result.stderr
            events = [json.loads(line) for line in stdout.splitlines() if line.strip()]
            ran = {event['Test'] for event in events if event.get('Action') == 'run' and event.get('Test')}
            failed = {event['Test'] for event in events if event.get('Action') == 'fail' and event.get('Test')}
            if native_code == 0 and len(ran) >= int(os.environ['IOT_MUTATION_MIN_TESTS']):
                reason = 'survived'; engine_code = 1
            elif native_code != 0 and failed:
                reason = 'killed_by_failing_test'; engine_code = 0
            else:
                reason = 'compilation_or_test_harness_failure'; engine_code = 2
        except subprocess.TimeoutExpired as error:
            stdout = error.stdout or b''; stderr = error.stderr or b''
            reason = 'test_timeout'; engine_code = 2
    finally:
        original.write_bytes(before)
        if sha256(original.read_bytes()).hexdigest() != os.environ['IOT_MUTATION_SOURCE_SHA256']:
            raise RuntimeError('The disposable source was not restored')
    (output / (pin + '.stdout')).write_bytes(stdout)
    (output / (pin + '.stderr')).write_bytes(stderr)
    record = {'mutant_sha256': pin, 'native_test_exit': native_code, 'engine_exit': engine_code,
        'reason': reason, 'elapsed_seconds': time.monotonic() - started,
        'checked_utc': datetime.now(timezone.utc).isoformat(),
        'stdout_sha256': sha256(stdout).hexdigest(), 'stderr_sha256': sha256(stderr).hexdigest()}
    with (output / 'records.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(record) + '\n')
    return engine_code


if __name__ == '__main__':
    raise SystemExit(main())
