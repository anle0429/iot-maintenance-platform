"""Validate an explicit source catalog and make stable, source-traceable Markdown chunks."""
from hashlib import sha256
from pathlib import Path
import json
import re


def read_catalog(directory):
    """Reject undeclared, changed, empty, oversized or escaped sources before embedding any content."""
    directory = Path(directory).resolve(); manifest = json.loads((directory / 'catalog.json').read_bytes())
    if manifest['schema_version'] != 1 or not manifest['documents']: raise ValueError('A nonempty version-one catalog is required')
    documents = []; ids = set()
    for row in manifest['documents']:
        identifier = row['document_id']
        if not re.fullmatch(r'[a-z][a-z0-9-]{1,63}', identifier) or identifier in ids: raise ValueError('Document IDs must be valid and unique')
        ids.add(identifier); path = (directory / row['file']).resolve()
        if not path.is_relative_to(directory) or path.suffix != '.md': raise ValueError('Sources must be Markdown files within the corpus')
        raw = path.read_bytes()
        if not 1 <= len(raw) <= 512_000 or sha256(raw).hexdigest() != row['sha256']: raise ValueError('Source size/hash mismatch: ' + identifier)
        text = raw.decode('utf-8')
        if not text.strip() or row['revision'] < 1 or row['source_type'] != 'authored-synthetic-reference': raise ValueError('Source revision/provenance is required')
        documents.append(dict(row, text=text))
    return documents


def chunk_documents(documents, max_chars=1100, overlap_chars=160):
    """Split on section boundaries, keeping long-section overlap and stable content/provenance identities."""
    if not 200 <= max_chars <= 4000 or not 0 <= overlap_chars < max_chars // 2: raise ValueError('Invalid chunk-size/overlap boundary')
    chunks = []
    for document in documents:
        sections = re.split(r'(?m)^(?=#{1,6} )', document['text'])
        for section in sections:
            if not section.strip(): continue
            if not '\n'.join(section.splitlines()[1:]).strip(): continue
            title = section.splitlines()[0].lstrip('# ').strip()
            start = 0
            while start < len(section):
                end = min(len(section), start + max_chars)
                if end < len(section):
                    boundary = section.rfind(' ', start + max_chars // 2, end)
                    if boundary != -1: end = boundary
                text = section[start:end].strip()
                identity = [document['document_id'], document['revision'], document['sha256'], title, start, text]
                chunk_id = sha256(json.dumps(identity, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
                chunks.append({'chunk_id': chunk_id, 'document_id': document['document_id'], 'revision': document['revision'],
                               'source_sha256': document['sha256'], 'source_file': document['file'], 'section': title, 'text': text})
                if end == len(section): break
                start = max(start + 1, end - overlap_chars)
    if not chunks or len({row['chunk_id'] for row in chunks}) != len(chunks): raise ValueError('Chunk identities must be nonempty and unique')
    return chunks
