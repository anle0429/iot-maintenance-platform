"""Resolve the reviewed documentation layout while retaining exact historical source bindings."""
from hashlib import sha256
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit
import json
import posixpath
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
LAYOUT_SHA256 = 'c2a40e350ef24cd5dbcc62f3d37ff0bf6f60d5dad89d9779e8d5b0bc195ec3e5'


def layout():
    """Authenticate the path inventory independently of editable documentation content."""
    body = (ROOT / 'docs/navigation.json').read_bytes().replace(b'\r\n', b'\n')
    if sha256(body).hexdigest() != LAYOUT_SHA256: raise ValueError('Documentation layout inventory changed')
    value = json.loads(body)
    assert value['format'] == 'iot-documentation-layout-v1'
    assert re.fullmatch('[0-9a-f]{40}', value['base_commit'])
    assert len(value['files']) == 114
    assert len({row['path'] for row in value['files'].values()}) == 114
    return value


def path_map(value=None):
    """Return only the declared old-to-current repository paths."""
    return {name: row['path'] for name, row in (value or layout())['files'].items()}


def bound_documents():
    """Name moved pages and unchanged-location pages with reviewed link-only edits."""
    value = layout()
    return set(value['files']) | set(value['linked_files'])


def current_path(name):
    """Resolve a legacy document name without changing code, evidence or historical Git paths."""
    return ROOT / path_map().get(name, name)


def relocate_markdown(body, old_name, new_name, mapping):
    """Rewrite local Markdown/HTML navigation and current paths; retain pinned and foreign URLs."""
    text = body.decode('utf-8').replace('\r\n', '\n')

    def target(value):
        """Rebase one URL from its original file and preserve query/fragment semantics."""
        angle = value.startswith('<') and value.endswith('>')
        raw = value[1:-1] if angle else value
        parts = urlsplit(raw)
        if parts.scheme or parts.netloc:
            prefix = 'https://github.com/anle0429/iot-maintenance-platform/'
            if raw.startswith(prefix):
                for branch in ['blob/main/', 'tree/main/', 'blob/HEAD/', 'tree/HEAD/']:
                    if parts.path.startswith('/anle0429/iot-maintenance-platform/' + branch):
                        head, _, name = parts.path.partition(branch)
                        raw = urlunsplit((parts.scheme, parts.netloc, head + branch + mapping.get(name, name), parts.query, parts.fragment))
            return '<' + raw + '>' if angle else raw
        if not parts.path or parts.path.startswith('/') or raw.startswith('data:'): return value
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(old_name), unquote(parts.path)))
        destination = mapping.get(resolved, resolved)
        relative = posixpath.relpath(destination, posixpath.dirname(new_name) or '.')
        if parts.path.endswith('/') and not relative.endswith('/'): relative += '/'
        raw = urlunsplit(('', '', relative, parts.query, parts.fragment))
        return '<' + raw + '>' if angle else raw

    text = re.sub(r'(!?\[[^\]\n]*\]\()([^\n)]+)(\))', lambda m: m[1] + target(m[2]) + m[3], text)
    text = re.sub(r'((?:href|src|srcset)=["\'])([^"\']+)(["\'])', lambda m: m[1] + target(m[2]) + m[3], text)
    text = re.sub(r'(^\[[^\]\n]+\]:\s*)(\S+)', lambda m: m[1] + target(m[2]), text, flags=re.M)
    # Skip complete URLs here: immutable commit links keep their captured path.
    alternatives = '|'.join(re.escape(name) for name in sorted(mapping, key=len, reverse=True))
    text = re.sub(r'https?://[^\s<>"\')]+|(?<![\w/])(?:' + alternatives + r')(?![\w.-])',
                  lambda m: m[0] if m[0].startswith(('http://', 'https://')) else mapping[m[0]], text)
    return text.encode('utf-8')


def navigation_equivalent(name, original, current):
    """Accept only the exact declared navigation transformation for a moved page."""
    mapping = path_map()
    return name in bound_documents() and relocate_markdown(original, name, mapping.get(name, name), mapping) == current.replace(b'\r\n', b'\n')


def historical_document(name):
    """Verify the moved page against its pinned predecessor before returning original receipt bytes."""
    value = layout(); row = (value['files'] | value['linked_files'])[name]
    original = subprocess.check_output(['git', '--no-replace-objects', 'show', value['base_commit'] + ':' + name], cwd=ROOT)
    assert sha256(original).hexdigest() == row['before_sha256'], name
    current = (ROOT / row['path']).read_bytes().replace(b'\r\n', b'\n')
    assert sha256(current).hexdigest() == row['after_sha256'], name
    assert navigation_equivalent(name, original, current), name
    return original
