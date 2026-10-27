"""Version datasets using immutable content-addressed blobs and complete, independently restorable manifests."""
from contextlib import contextmanager
from hashlib import sha256
import json,os,re,shutil,sqlite3,tempfile
from pathlib import Path,PurePosixPath


def canonical_bytes(value):
    """Make a stable manifest identity without wall-clock, object-order or NaN variations."""
    return (json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()


def file_digest(path):
    """Hash actual bytes with bounded memory."""
    result=sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):result.update(block)
    return result.hexdigest()


def relative_name(name):
    """Reject traversal, absolute paths, platform separators and noncanonical logical dataset names."""
    if not isinstance(name,str):raise ValueError('Unsafe logical dataset path')
    path=PurePosixPath(name)
    if (not name or '\\' in name or ':' in name or '\0' in name
        or path.is_absolute() or any(p in ['.','..'] for p in path.parts) or path.as_posix()!=name):
        raise ValueError('Unsafe logical dataset path')
    return name


class ContentAddressedDatasetStore:
    """Store each distinct byte sequence once; a manifest always describes a complete logical snapshot."""
    def __init__(self,root):
        """Keep blobs and manifests in a dedicated store with a cross-process SQLite publication lock."""
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        for name in ['blobs','manifests']:(self.root/name).mkdir(exist_ok=True)

    @contextmanager
    def locked(self):
        """Serialize publish/read operations without stale lock files after a process is killed."""
        connection=sqlite3.connect(self.root/'publication.sqlite',timeout=30)
        try:
            connection.execute('BEGIN IMMEDIATE');yield
            connection.commit()
        except BaseException:
            connection.rollback();raise
        finally:connection.close()

    def blob(self,digest):
        """Resolve a checked content hash, never a caller supplied filesystem path."""
        if not re.fullmatch('[0-9a-f]{64}',digest):raise ValueError('Invalid blob hash')
        return self.root/'blobs'/digest[:2]/digest

    def manifest(self,version):
        """Verify the content-derived version before using any referenced files."""
        if not isinstance(version,str) or not re.fullmatch('[0-9a-f]{64}',version):raise ValueError('Invalid dataset version')
        path=self.root/'manifests'/(version+'.json')
        if path.is_symlink():raise ValueError('Manifest symlinks are not allowed')
        content=path.read_bytes()
        if sha256(content).hexdigest()!=version:raise ValueError('Dataset manifest was changed')
        result=json.loads(content)
        if result.get('format')!='iot-content-dataset-v1' or not isinstance(result.get('files'),dict):raise ValueError('Unknown manifest schema')
        for name,item in result['files'].items():
            relative_name(name);self.blob(item['sha256'])
            if type(item['size']) is not int or item['size']<0:raise ValueError('Invalid blob size')
        return result

    def publish(self,files,metadata,parent_version=None):
        """Write only unseen blobs; record replacements, additions and deletions relative to a parent."""
        if not files or not isinstance(metadata,dict):raise ValueError('Require a nonempty dataset and metadata')
        entries={}
        for name,source in sorted(files.items()):
            relative_name(name);source=Path(source)
            if source.is_symlink() or not source.is_file():raise ValueError('Only regular source files are allowed')
            entries[name]={'sha256':file_digest(source),'size':source.stat().st_size}
        document={'format':'iot-content-dataset-v1','metadata':metadata,'files':entries}
        content=canonical_bytes(document);version=sha256(content).hexdigest();new=[];reused=[]
        with self.locked():
            previous=self.manifest(parent_version)['files'] if parent_version else {}
            for name,item in entries.items():
                blob=self.blob(item['sha256']);blob.parent.mkdir(exist_ok=True)
                if blob.exists() or blob.is_symlink():
                    if blob.is_symlink() or blob.stat().st_size!=item['size'] or file_digest(blob)!=item['sha256']:
                        raise ValueError('Existing content-addressed blob is corrupt; preserve it for diagnosis')
                    reused.append(item['sha256']);continue
                with tempfile.NamedTemporaryFile(dir=blob.parent,prefix='.pending-',delete=False) as stream:
                    pending=Path(stream.name)
                    with Path(files[name]).open('rb') as source:shutil.copyfileobj(source,stream,1024*1024)
                    stream.flush();os.fsync(stream.fileno())
                if pending.stat().st_size!=item['size'] or file_digest(pending)!=item['sha256']:
                    raise ValueError('Source changed during publication; unsealed pending file retained')
                pending.replace(blob);new.append(item['sha256'])
            target=self.root/'manifests'/(version+'.json')
            if target.exists() or target.is_symlink():
                if target.is_symlink() or target.read_bytes()!=content:raise ValueError('Existing version manifest differs')
            else:
                with target.open('xb') as stream:stream.write(content)
            changed=[n for n,v in entries.items() if previous.get(n)!=v]
            deleted=sorted(set(previous)-set(entries))
            sizes={x['sha256']:x['size'] for x in entries.values()}
            return {'passed':True,'version':version,'parent_version':parent_version,'logical_files':len(entries),
                'logical_bytes':sum(x['size'] for x in entries.values()),'new_blob_sha256':new,'reused_blob_sha256':reused,
                'new_blob_bytes':sum(sizes[h] for h in new),'new_blob_count':len(new),
                'changed_paths':changed,'deleted_paths':deleted,'unchanged_paths':sorted(set(entries)-set(changed)),
                'manifest_sha256':version,'metadata':metadata}

    def restore(self,version,destination):
        """Reconstruct a full version without its parent and check every copied file before sealing."""
        destination=Path(destination)
        with self.locked():
            manifest=self.manifest(version)
            for item in manifest['files'].values():
                blob=self.blob(item['sha256'])
                if blob.is_symlink() or blob.stat().st_size!=item['size'] or file_digest(blob)!=item['sha256']:
                    raise ValueError('Dataset blob is corrupt or missing')
            destination.mkdir(parents=True,exist_ok=False)
            for name,item in manifest['files'].items():
                path=destination/name;path.parent.mkdir(parents=True,exist_ok=True)
                with path.open('xb') as stream,self.blob(item['sha256']).open('rb') as source:shutil.copyfileobj(source,stream,1024*1024)
                if file_digest(path)!=item['sha256']:raise ValueError('Restored bytes differ')
            return {'passed':True,'version':version,'files':len(manifest['files']),
                'bytes':sum(x['size'] for x in manifest['files'].values()),'all_file_bytes_verified':True}
