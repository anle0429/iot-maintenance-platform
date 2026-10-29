"""Plot the sampled native worker counts; this figure is a data visualization, not a runtime screenshot."""
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import json
import tarfile
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[2]


def render():
    """Read only the authenticated original observation series and export English PNG/SVG figures."""
    archive = (ROOT / 'docs/evidence/agent-scaling/native-receipts.tar.gz').read_bytes()
    assert sha256(archive).hexdigest() == '3cb5535e4f0c70a3b391171374c88d44824ae8ee538e4e9a14182c609d29cf99'
    prefix = 'agent-scaling-capture-29a/native/agent-burst-29c/'
    with tarfile.open(fileobj=BytesIO(archive), mode='r:gz') as package:
        observations = json.load(package.extractfile(prefix + 'observations.json'))
        report = json.load(package.extractfile(prefix + 'verification.json'))
    assert report['passed'] and report['verified_coordinator_requests'] == 18
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10, 'svg.hashsalt': 'iot-agent-scaling-29c'})
    figure, axes = plt.subplots(3, 1, figsize=(11.8, 7.0), sharex=True)
    times = [row['elapsed_seconds'] / 60 for row in observations]
    for axis, role in zip(axes, ['coordinator', 'retrieval', 'drift']):
        axis.step(times, [row['roles'][role]['desired'] for row in observations], where='post', color='#a3aab8', linewidth=3.0, label='Desired workers')
        axis.step(times, [row['roles'][role]['ready'] for row in observations], where='post', color='#6845b8', linewidth=1.8, linestyle='--', label='Ready workers')
        axis.step(times, [row['roles'][role]['running'] for row in observations], where='post', color='#147d92', linewidth=1.4, label='Running actors')
        axis.set_ylim(-.15, 4.7); axis.set_yticks([0, 1, 2, 3, 4]); axis.set_ylabel(role.capitalize())
        axis.grid(axis='y', color='#e9ecf1', linewidth=.8); axis.set_axisbelow(True)
        axis.spines[['top', 'right']].set_visible(False)
        axis.spines[['left', 'bottom']].set_color('#c5cad3')
    axes[0].legend(loc='upper center', bbox_to_anchor=(.5, 1.36), ncol=3, frameon=False)
    axes[-1].set_xlabel('Elapsed time (minutes)'); axes[-1].set_xlim(0, times[-1] + .15)
    figure.suptitle('Agent worker capacity under coordinator load', x=.10, y=.975, ha='left', fontsize=17, fontweight='bold', color='#202b3c')
    figure.text(.10, .925, '18 verified requests · 14 explicit capacity rejections handled · 2 → 4 → 2 workers per role', fontsize=10, color='#536071')
    figure.text(.10, .025, 'Source: native agent-burst-29c · 29 October 2026 · sampled counts; intervals vary · single control VM', fontsize=8.5, color='#647080')
    figure.subplots_adjust(left=.10, right=.97, top=.84, bottom=.10, hspace=.22)
    output = ROOT / 'docs/images/agent-worker-scaling'
    figure.savefig(output.with_suffix('.png'), dpi=180, facecolor='white')
    figure.savefig(output.with_suffix('.svg'), facecolor='white', metadata={'Date': None})
    svg = output.with_suffix('.svg')
    svg.write_bytes(svg.read_bytes().replace(b'\r\n', b'\n'))
    plt.close(figure)
    output.with_suffix('.json').write_text(json.dumps({'kind': 'plot-of-native-observations', 'runtime_screenshot': False,
        'source_archive_sha256': sha256(archive).hexdigest(), 'source_run': 'agent-burst-29c', 'observations': len(observations),
        'observed_seconds': report['observed_seconds'], 'configured_arrival_seconds': report['configured_burst_seconds']}, indent=2) + '\n', encoding='utf-8', newline='\n')
    print(json.dumps({'rendered': True, 'observations': len(observations), 'output': str(output)}))


if __name__ == '__main__': render()
