"""Record only pinned OPA command dispatches for a bounded policy demonstration."""
from collections import Counter
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import os
import subprocess


class PolicyExecutionLedger:
    """Reject unapproved process launches and retain captured OPA invocation results."""

    allowed_verbs = frozenset({'version', 'check', 'eval'})

    def __init__(self, opa_path, expected_sha256, *, runner=None):
        """Resolve one executable identity and verify its expected bytes before use."""
        self.opa_path = Path(opa_path).resolve(strict=True)
        if (not isinstance(expected_sha256, str) or len(expected_sha256) != 64
                or any(c not in '0123456789abcdef' for c in expected_sha256)):
            raise ValueError('Require one lowercase SHA-256 for the pinned OPA executable')
        self.expected_sha256 = expected_sha256
        self._runner = subprocess.run if runner is None else runner
        self._invocations = []
        self._verify_binary()

    def _verify_binary(self):
        """Recheck the selected executable before every dispatch, including later replacements."""
        try:
            valid = self.opa_path.is_file() and sha256(self.opa_path.read_bytes()).hexdigest() == self.expected_sha256
        except OSError:
            valid = False
        if not valid:
            raise ValueError('The OPA executable differs from its pinned SHA-256')

    def _record(self, command, exit_code, stdout, stderr, error=None):
        """Store only captured stream digests and actual return values from the process runner."""
        stdout = b'' if stdout is None else stdout
        stderr = b'' if stderr is None else stderr
        if not isinstance(stdout, bytes) or not isinstance(stderr, bytes):
            raise ValueError('The OPA process runner must return captured binary output')
        row = {'sequence': len(self._invocations) + 1, 'verb': command[1], 'argv': list(command),
               'exit_code': exit_code, 'stdout_sha256': sha256(stdout).hexdigest(),
               'stderr_sha256': sha256(stderr).hexdigest()}
        if error is not None:
            row['error'] = error
        self._invocations.append(row)

    def __call__(self, argv, **options):
        """Dispatch only this pinned OPA's version/check/eval commands without a shell."""
        if options.get('shell') or 'executable' in options:
            raise ValueError('OPA dispatch cannot use a shell or alternate executable')
        if set(options) - {'capture_output', 'timeout', 'check', 'shell'}:
            raise ValueError('OPA dispatch accepts only captured binary output and bounded run options')
        if options.get('capture_output', True) is not True:
            raise ValueError('OPA dispatch must capture both original output streams')
        if not isinstance(argv, (list, tuple)) or len(argv) < 2:
            raise ValueError('Require an explicit OPA executable and approved command verb')
        try:
            command = [os.fspath(value) for value in argv]
            if any(not isinstance(value, str) or '\x00' in value for value in command):
                raise ValueError('Require ordinary string OPA arguments')
            executable = Path(command[0])
            if not executable.is_absolute() or executable.resolve(strict=True) != self.opa_path:
                raise ValueError('A different executable cannot use the pinned OPA process runner')
        except (OSError, TypeError):
            raise ValueError('Require the resolved pinned OPA executable') from None
        if command[1] not in self.allowed_verbs:
            raise ValueError('Only OPA version, check and eval may reach the process runner')
        self._verify_binary()
        command[0] = str(self.opa_path)
        options.setdefault('capture_output', True)
        try:
            result = self._runner(command, **options)
        except subprocess.CalledProcessError as error:
            self._record(command, error.returncode, error.stdout, error.stderr, type(error).__name__)
            raise
        except subprocess.TimeoutExpired as error:
            self._record(command, None, error.stdout, error.stderr, type(error).__name__)
            raise
        self._record(command, result.returncode, result.stdout, result.stderr)
        return result

    def summary(self):
        """Derive a detached process-only summary from the retained invocation records."""
        return {'pinned_opa_path': str(self.opa_path), 'pinned_opa_sha256': self.expected_sha256,
                'allowed_verbs': sorted(self.allowed_verbs), 'invocation_count': len(self._invocations),
                'verb_counts': dict(Counter(row['verb'] for row in self._invocations)),
                'invocations': deepcopy(self._invocations),
                'scope': 'Only commands dispatched through this ledger are recorded. Captured process output '
                         'hashes do not measure network/model calls or deployment state.'}
