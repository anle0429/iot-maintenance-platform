"""Compare a native DuckDB warehouse query before and after indexing retained Gold data."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import time

import duckdb


ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / 'docs/evidence/data-platform-review/schema/input-files.json'
TABLES = ('dim_factory', 'dim_machine', 'fact_failure', 'fact_maintenance', 'fact_telemetry')
CONFIG = {'threads': 1, 'memory_limit': '256MB'}
INDEX = 'CREATE INDEX telemetry_event_id_idx ON gold.fact_telemetry(event_id)'
PROJECTION = '''f.event_id, f.machine_id, f.event_ts, f.temperature_c,
       f.vibration_rms_mm_s, f.pressure_bar, d.factory_id, d.valid_from_ts'''
QUERY = f'''SELECT {PROJECTION}
FROM gold.fact_telemetry AS f JOIN gold.dim_machine AS d ON f.machine_sk = d.machine_sk
WHERE f.event_id = ?'''
PAIRS = 8
LOOKUPS = 64


def sha256(path):
    """Hash a source or measurement file without loading it all into memory."""
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def verified_files(source):
    """Bind all Gold parts to the original run inventory and reject extras."""
    files = json.loads(INVENTORY.read_bytes())['files']
    gold = [row for row in files if Path(row['path']).parts[:2] in [('gold', t) for t in TABLES]]
    for table in TABLES:
        paths = {str((source / row['path']).resolve()) for row in gold if Path(row['path']).parent.name == table}
        if not paths or paths != {str(p.resolve()) for p in (source / 'gold' / table).glob('*.parquet')}:
            raise ValueError(f'Gold file inventory differs for {table}')
    for row in gold:
        path = source / row['path']
        if path.is_symlink() or path.stat().st_size != row['bytes'] or sha256(path) != row['sha256']:
            raise ValueError(f'Retained Gold input differs: {row["path"]}')
    return gold


def build_warehouse(database, source):
    """Materialize and independently compare every table with the sealed Parquet source."""
    with duckdb.connect(str(database), config=CONFIG) as conn:
        conn.execute('CREATE SCHEMA gold')
        counts = {}
        for table in TABLES:
            parquet = str(source / 'gold' / table / '*.parquet')
            original = 'SELECT * FROM read_parquet(?)'
            copied = f'SELECT * FROM gold.{table}'
            conn.execute(f'CREATE TABLE gold.{table} AS {original}', [parquet])
            for left, right in ((copied, original), (original, copied)):
                if conn.execute(f'({left}) EXCEPT ALL ({right}) LIMIT 1', [parquet]).fetchone():
                    raise ValueError(f'Warehouse load differs for {table}')
            counts[table] = conn.execute(f'SELECT count(*) FROM gold.{table}').fetchone()[0]
        count, distinct = conn.execute('SELECT count(*), count(DISTINCT event_id) FROM gold.fact_telemetry').fetchone()
        if count != distinct or count != 57_600:
            raise ValueError('Expected the original unique event-ID grain')
        conn.execute('ANALYZE')
        conn.execute('CHECKPOINT')
    return counts


def oracle(source):
    """Choose keys deterministically and read their exact responses directly from Parquet."""
    fact = str(source / 'gold/fact_telemetry/*.parquet')
    dim = str(source / 'gold/dim_machine/*.parquet')
    with duckdb.connect(config=CONFIG) as conn:
        rows = conn.execute(f'''SELECT {PROJECTION}
            FROM read_parquet(?) AS f JOIN read_parquet(?) AS d ON f.machine_sk = d.machine_sk
            ORDER BY md5(f.event_id), f.event_id LIMIT ?''', [fact, dim, LOOKUPS]).fetchall()
    keys = [row[0] for row in rows]
    if len(keys) != LOOKUPS or len(set(keys)) != LOOKUPS:
        raise ValueError('Direct Parquet oracle did not produce distinct event IDs')
    return keys, rows


def scan_type(profile):
    """Locate the fact scan in DuckDB's native JSON profile."""
    if profile.get('extra_info', {}).get('Table') == 'fact_telemetry':
        return profile['extra_info']['Type']
    for child in profile.get('children', []):
        result = scan_type(child)
        if result:
            return result
    return None


def run_queries(conn, keys, expected):
    """Time the same parameterized client workload and check every row outside the timer."""
    started = time.perf_counter_ns()
    results = [conn.execute(QUERY, [key]).fetchall() for key in keys]
    elapsed = time.perf_counter_ns() - started
    if results != [[row] for row in expected]:
        raise ValueError('Warehouse query result differs from the direct Parquet oracle')
    return elapsed


def run(source, output):
    """Create isolated baseline and index copies, retain plans and measure alternating pairs."""
    source, output = source.resolve(), output.resolve()
    if output.is_relative_to(source):
        raise ValueError('Choose an output outside the input directory')
    original_files = verified_files(source)
    output.mkdir(parents=True, exist_ok=False)
    baseline, indexed = output / 'baseline.duckdb', output / 'indexed.duckdb'
    counts = build_warehouse(baseline, source)
    shutil.copyfile(baseline, indexed)
    with duckdb.connect(str(indexed), config=CONFIG) as conn:
        started = time.perf_counter_ns()
        conn.execute(INDEX)
        build_ns = time.perf_counter_ns() - started
        conn.execute('CHECKPOINT')
    keys, expected = oracle(source)
    db_bytes = {'baseline': baseline.stat().st_size, 'indexed': indexed.stat().st_size}
    samples = []
    with (duckdb.connect(str(baseline), read_only=True, config=CONFIG) as before,
          duckdb.connect(str(indexed), read_only=True, config=CONFIG) as after):
        connections = {'baseline': before, 'indexed': after}
        scans = {}
        for label, conn in connections.items():
            raw = conn.execute('EXPLAIN (ANALYZE, FORMAT JSON) ' + QUERY, [keys[0]]).fetchone()[1]
            scans[label] = scan_type(json.loads(raw))
            (output / f'{label}-plan.json').write_text(raw + '\n', encoding='utf-8', newline='\n')
        if scans != {'baseline': 'Sequential Scan', 'indexed': 'Index Scan'}:
            raise ValueError(f'DuckDB fact scan did not switch to the index: {scans}')
        for index in range(-2, PAIRS):
            order = ('baseline', 'indexed') if index % 2 == 0 else ('indexed', 'baseline')
            entry = {'order': list(order), 'pair': index + 1}
            for label in order:
                entry[label] = run_queries(connections[label], keys, expected)
            if index >= 0:
                samples.append(entry)
    if verified_files(source) != original_files:
        raise ValueError('Gold input inventory changed during the benchmark')
    medians = {label: statistics.median(row[label] for row in samples) for label in db_bytes}
    response_bytes = json.dumps(expected, default=str, allow_nan=False).encode('utf-8')
    report = {
        'passed': True,
        'scope': 'Local embedded SQL warehouse, warm-cache point lookup and dimensional join on retained day2-final Gold.',
        'completed_utc': datetime.now(timezone.utc).isoformat(),
        'runtime': {'duckdb': duckdb.__version__, 'python': platform.python_version(),
                    'system': platform.system(), 'machine': platform.machine(), 'settings': CONFIG},
        'source_git_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'script_sha256': sha256(Path(__file__)),
        'inventory': INVENTORY.relative_to(ROOT).as_posix(),
        'inventory_sha256': sha256(INVENTORY),
        'source_files': original_files,
        'exact_multiset_equal_at_load': True,
        'source_unchanged_after_run': True,
        'table_rows': counts,
        'query': QUERY,
        'index': INDEX,
        'fact_scan_types': scans,
        'plan_sha256': {label: sha256(output / f'{label}-plan.json') for label in scans},
        'key_selection': 'First 64 distinct retained event IDs by md5(event_id), event_id',
        'lookups_per_profile_per_pair': LOOKUPS,
        'warmup_passes_per_profile': 2,
        'pairs_ns': samples,
        'median_batch_ns': medians,
        'indexed_change_percent': 100 * (medians['indexed'] / medians['baseline'] - 1),
        'index_creation_ns': build_ns,
        'checkpointed_database_bytes': db_bytes,
        'index_database_growth_bytes': db_bytes['indexed'] - db_bytes['baseline'],
        'exact_responses_equal_to_parquet_oracle': True,
        'oracle_rows_sha256': hashlib.sha256(response_bytes).hexdigest(),
        'oracle_rows': json.loads(response_bytes),
    }
    (output / 'report.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n',
                                        encoding='utf-8', newline='\n')
    return report


def main():
    """Run the bounded local workload using an unchanged retained Gold directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / 'data/lakehouse/day2-final')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = run(args.source, args.output)
    print(json.dumps({key: result[key] for key in ('passed', 'table_rows', 'median_batch_ns',
          'indexed_change_percent', 'index_creation_ns', 'checkpointed_database_bytes')}, indent=2))


if __name__ == '__main__':
    main()
