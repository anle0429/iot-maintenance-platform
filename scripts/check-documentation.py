"""Check maintained English documentation, local links and generated figures while preserving original source/evidence artifacts."""
from pathlib import Path
from urllib.parse import unquote, urlsplit
import argparse
import ast
import io
import json
import re
import struct
import subprocess
import tokenize

ROOT = Path(__file__).resolve().parents[1]
ACCENTS = set(chr(value) for value in range(0x1EA0, 0x1EFA)) | set('\u00e0\u00e1\u00e2\u00e3\u00e8\u00e9\u00ea\u00ec\u00ed\u00f2\u00f3\u00f4\u00f5\u00f9\u00fa\u00fd\u0103\u0111\u0129\u0169\u01a1\u01b0')
ACCENTS |= {letter.upper() for letter in ACCENTS}


def slug(value):
    """Resolve the simple GitHub-style heading fragments used in the maintained Markdown pages."""
    return re.sub(r'[^\w\- ]', '', value.lower()).replace(' ', '-')


def headings(text):
    """Ignore fenced code when collecting actual Markdown section destinations."""
    clean = re.sub(r'```.*?```', '', text, flags=re.S)
    return {slug(match.group(1)) for match in re.finditer(r'^#{1,6} (.+)$', clean, re.M)}


def language_text(path):
    """Scan authored prose and code descriptions; do not classify Unicode test data as explanatory language."""
    text = path.read_text(encoding='utf-8')
    if path.suffix == '.py':
        tree = ast.parse(text); values = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                value = ast.get_docstring(node)
                if value: values.append(value)
        values.extend(token.string for token in tokenize.generate_tokens(io.StringIO(text).readline) if token.type == tokenize.COMMENT)
        return '\n'.join(values)
    if path.suffix == '.ipynb':
        return '\n'.join(''.join(cell['source']) for cell in json.loads(text)['cells'] if cell['cell_type'] == 'markdown')
    return text


def check():
    """Report all failures together without modifying a source file, screenshot, link or stored receipt."""
    names = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=ROOT).decode().split('\0')
    names = sorted(set(name for name in names if name and not name.startswith(('docs/evidence/', 'references/', 'data/', 'outputs/'))))
    errors = []; links = 0; prose = 0
    for name in names:
        path = ROOT / name
        if not path.is_file() or path.suffix not in {'.md', '.py', '.json', '.ipynb', '.yaml', '.yml', '.ps1', '.sql', '.txt', '.toml', '.tf', '.rego'}: continue
        try: text = language_text(path)
        except (UnicodeError, SyntaxError, tokenize.TokenError) as error:
            errors.append({'path': name, 'error': type(error).__name__}); continue
        prose += 1
        if any(letter in ACCENTS for letter in text):
            examples = [line.strip()[:150] for line in text.splitlines() if any(letter in ACCENTS for letter in line)]
            errors.append({'path': name, 'error': 'Source-language prose remains', 'examples': examples[:3]})
        if path.suffix != '.md': continue
        for target in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', text):
            target = target.strip('<>')
            if urlsplit(target).scheme: continue
            filename, _, fragment = unquote(target).partition('#')
            destination = (path.parent / filename).resolve() if filename else path
            links += 1
            if not destination.is_relative_to(ROOT) or not destination.exists():
                errors.append({'path': name, 'error': 'Missing or out-of-repository target', 'target': target})
            elif fragment and destination.suffix == '.md' and not str(destination.relative_to(ROOT)).replace('\\','/').startswith(('docs/evidence/', 'references/')):
                if fragment not in headings(destination.read_text(encoding='utf-8')):
                    errors.append({'path': name, 'error': 'Missing heading', 'target': target})
    figures = json.loads((ROOT / 'docs/images/diagram-index.json').read_bytes())
    assert figures['runtime_screenshot'] is False
    for view in figures['views']:
        raw = (ROOT / 'docs/images' / (view['name'] + '.png')).read_bytes()
        assert raw[:8] == b'\x89PNG\r\n\x1a\n'
        assert struct.unpack('>II', raw[16:24]) == (view['width'], view['height'])
        assert (ROOT / 'docs/images' / (view['name'] + '.svg')).is_file()
        assert len(view['arrows']) == len(set(view['arrows']))
    return {'passed': not errors, 'maintained_text_files': prose, 'local_links': links, 'generated_views': len(figures['views']),
            'original_evidence_and_sources_excluded': True, 'runtime_screenshots_edited': False, 'errors': errors}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--output', type=Path)
    args = parser.parse_args(); report = check()
    if args.output:
        output = args.output.resolve()
        if not output.is_relative_to(ROOT / 'outputs') or output.exists(): raise ValueError('Use a new JSON receipt under outputs/')
        output.parent.mkdir(parents=True, exist_ok=True); output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, indent=2)); raise SystemExit(0 if report['passed'] else 1)
