"""Plot unchanged recorded receipts; reproduce with Python, Matplotlib 3.10.7 and restored evidence."""
from hashlib import sha256
from pathlib import Path
import json

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
SOURCES = {}
BLUE, GREEN, RED = '#2563eb', '#047857', '#b45309'


def read(relative):
    """Read a named original receipt and bind its exact bytes to the plot manifest."""
    body = (ROOT / relative).read_bytes()
    SOURCES[relative] = sha256(body).hexdigest()
    return json.loads(body)


def finish(fig, name, title, subtitle):
    """Keep provenance visible and save a standalone explanatory plot, never a mock UI capture."""
    fig.suptitle(title, x=.07, ha='left', fontsize=21, weight='bold', color='#0f172a')
    fig.text(.07, .88, subtitle, ha='left', fontsize=11, color='#475569')
    fig.text(.07, .035, 'Derived from original recorded JSON | Not a runtime screenshot | Source hashes: data-review-sources.json',
             fontsize=9, color='#475569')
    fig.tight_layout(rect=(.04, .08, .98, .84), w_pad=3)
    fig.savefig(OUT / (name + '.png'), dpi=150, facecolor='white')
    plt.close(fig)


def plot_generator(q):
    """Display skew and quality ratios with each denominator named explicitly."""
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.8))
    counts = q['factory_unique_counts']
    values = [100 * n / q['unique_telemetry_rows'] for n in counts.values()]
    bars = axes[0].bar(list(counts), values, color=[BLUE, '#94a3b8', '#94a3b8'])
    axes[0].bar_label(bars, labels=[f'{n:.0f}%' for n in values], padding=4)
    axes[0].set(title='Factory distribution', ylabel='% of unique readings', ylim=(0, 85))
    names = ['Offline extras', 'Stream retries', 'Late unique events']
    ratios = [q['offline_extras_per_unique'], q['stream_extras_per_unique'], q['late_fraction_of_unique']]
    bars = axes[1].barh(names, [n * 100 for n in ratios], color=[BLUE, GREEN, RED])
    axes[1].bar_label(bars, labels=[f'{n * 100:g}%' for n in ratios], padding=4)
    axes[1].set(xlabel='% of 57,600 unique events', xlim=(0, 6.4), title='Distinct transport problems')
    finish(fig, 'generator-characteristics', 'Generated source: measured characteristics',
           '20 machines / 48 hours | 58,752 offline exports | 58,464 stream deliveries | 300-600 s late delay')


def plot_skew(skew):
    """Pair all observed query times without concealing the first or slowest run."""
    fig, ax = plt.subplots(figsize=(12, 5.8))
    for strategy, label, color in [('baseline', 'Sort/merge baseline', BLUE), ('broadcast', 'Broadcast policy join', GREEN)]:
        rows = sorted((r for r in skew['measurements'] if r['strategy'] == strategy), key=lambda r: r['repeat'])
        times = [r['seconds'] for r in rows]
        ax.plot([1, 2, 3], times, 'o-', label=label, color=color, linewidth=2.5, markersize=8)
        for i, value in enumerate(times, 1):
            ax.annotate(f'{value:.3f} s', (i, value), xytext=(0, 10), textcoords='offset points', ha='center', fontsize=11)
    ax.set(xticks=[1, 2, 3], xlabel='Paired repetition', ylabel='Elapsed seconds', ylim=(0, 22))
    ax.legend(frameon=False, loc='lower right'); ax.grid(axis='y', alpha=.2)
    finish(fig, 'skew-before-after', 'Skew handling: same query, different join strategy',
           '17.28 million readings | Identical 4,320-row result in all six runs | Median 15.339 s -> 8.577 s')


def plot_batch(q, batch):
    """Compare persisted counts and schema presence without inventing a speed benchmark."""
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.8))
    counts = [batch['counts']['bronze_telemetry'], batch['counts']['silver_telemetry']]
    bars = axes[0].bar(['Bronze exports', 'Silver unique readings'], counts, color=[BLUE, GREEN])
    axes[0].bar_label(bars, labels=[f'{n:,}' for n in counts], padding=4)
    axes[0].set(ylabel='Rows (axis starts at zero)', ylim=(0, 70000), title='1,152 extra exports removed')
    axes[1].axis('off')
    axes[1].text(.02, .92, 'Schema evolution', weight='bold', fontsize=16)
    axes[1].text(.02, .70, 'Raw v1: pressure_bar absent\nRaw v2: pressure_bar present', fontsize=13, linespacing=1.6)
    axes[1].text(.02, .40, 'Typed Silver union:\nv1 -> NULL (unknown)\nv2 -> observed pressure', fontsize=13, linespacing=1.6, color=GREEN)
    axes[1].text(.02, .08, '28,800 unique rows per schema version\nNo pressure is invented for older firmware.', fontsize=10, color='#475569')
    finish(fig, 'batch-before-after', 'Batch normalization: preserve readings, resolve exports',
           'Original small Spark run | Correctness comparison, not an isolated schema/dedup latency benchmark')


def plot_stream(stream):
    """Show exact window outputs from the same native baseline and corrected fixture."""
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.8))
    names = ['P-001\n00:00-00:10', 'P-001\n00:10-00:20', 'P-002\n00:00-00:10']
    rows = [[stream[k]['windows'][i] for i in range(3)] for k in ['baseline', 'corrected']]
    for ax, field, title, unit in [(axes[0], 'reading_count', 'Unique reading count', 'Readings'),
                                   (axes[1], 'temperature_sum', 'Temperature sum', 'Sum of Celsius readings')]:
        for offset, entries, label, color in [(-.18, rows[0], 'Baseline', BLUE), (.18, rows[1], 'Corrected / oracle', GREEN)]:
            values = [r[field] for r in entries]
            bars = ax.bar([i + offset for i in range(3)], values, width=.34, color=color, label=label)
            ax.bar_label(bars, labels=[f'{v:g}' for v in values], padding=3)
        ax.set(xticks=range(3), xticklabels=names, ylabel=unit, title=title)
        ax.margins(y=.2); ax.legend(frameon=False, fontsize=9)
    finish(fig, 'stream-before-after', 'Flink: baseline errors and corrected window results',
           'Same 22 messages | D accepted before closure | E quarantined after closure | Retry/replay IDs deduplicated')


def plot_docker(report):
    """Compare the two measured uncompressed footprints without implying transfer size or latency savings."""
    fig, ax = plt.subplots(figsize=(12, 5.8))
    values = [report['images'][name]['bytes'] for name in ['baseline', 'optimized']]
    bars = ax.barh(['Full Python baseline', 'Slim serving image'], [v / 1_000_000 for v in values], color=['#94a3b8', GREEN], height=.5)
    ax.invert_yaxis()
    ax.bar_label(bars, labels=[f'{v:,} bytes' for v in values], padding=8)
    ax.set(xlabel='Uncompressed Docker image size (decimal MB)', xlim=(0, 1950))
    finish(fig, 'docker-image-size', 'Predictor image: 85.5% smaller with the same responses',
           'Constructed full/slim pair | Python 3.12.16 | Same application, dependency lock and model | 32 HTTP samples each')


def main():
    """Regenerate five plots and record exact source identities for independent review."""
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'axes.spines.top': False, 'axes.spines.right': False})
    quality = read('docs/evidence/day1/quality.json')
    batch = read('docs/evidence/day2-final/batch-report.json')
    skew = read('docs/evidence/skew-benchmark/benchmark.json')
    stream = read('docs/evidence/day3/stream-report.json')
    docker = read('docs/evidence/data-platform-review/docker/verification.json')
    assert all(d['passed'] for d in [quality, batch, skew, stream, docker])
    plot_generator(quality); plot_skew(skew); plot_batch(quality, batch); plot_stream(stream)
    plot_docker(docker)
    manifest = {'kind': 'plots-derived-from-recorded-receipts', 'runtime_screenshots': False, 'source_sha256': SOURCES,
                'plots': ['generator-characteristics.png', 'skew-before-after.png', 'batch-before-after.png', 'stream-before-after.png', 'docker-image-size.png']}
    (OUT / 'data-review-sources.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
