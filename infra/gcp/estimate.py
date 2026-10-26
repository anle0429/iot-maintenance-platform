"""Estimate the initial VM session and retained disk costs separately from actual Cloud Billing."""

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path


def estimate(running_hours: Decimal, retained_hours: Decimal, profile: str = 'basic') -> dict:
    """Charge compute/IP only while running, and all provisioned disk for its entire retained lifetime."""
    if not all(value.is_finite() for value in (running_hours, retained_hours)):
        raise ValueError('Hours must be finite')
    if not 0 <= running_hours <= retained_hours or retained_hours <= 0:
        raise ValueError('Require 0 <= running hours <= retained hours and positive retained hours')
    rates = json.loads((Path(__file__).parent/'pricing-us-central1.json').read_text())
    profiles = json.loads((Path(__file__).parent/'capacity-profiles.json').read_text())
    if profile not in profiles:
        raise ValueError('Unknown reviewed capacity profile')
    capacity = profiles[profile]
    disk_rates = {'pd-standard': Decimal(rates['standard_disk_per_gib_hour']),
                  'pd-balanced': Decimal(rates['balanced_disk_per_gib_hour'])}
    vm = running_hours*Decimal(rates['vm_hourly_by_machine_type'][capacity['machine_type']])
    ipv4 = running_hours*Decimal(rates['external_ipv4_per_running_hour'])
    boot = retained_hours*capacity['boot_gib']*disk_rates[capacity['boot_type']]
    data = retained_hours*capacity['data_gib']*disk_rates[capacity['data_type']]
    scratch = retained_hours*capacity['scratch_gib']*disk_rates[capacity['scratch_type']]
    subtotal = vm+ipv4+boot+data+scratch
    return {
        'kind': 'estimate_only', 'captured_utc': datetime.now(timezone.utc).isoformat(),
        'rates': rates, 'profile': profile, 'configuration': {**capacity, 'running_hours': str(running_hours),
            'retained_disk_hours': str(retained_hours)},
        'estimated_usd': {key: str(value.quantize(Decimal('0.000001'))) for key,value in {
            'vm': vm, 'ephemeral_ipv4': ipv4, 'boot_disk': boot, 'data_disk': data, 'scratch_disk': scratch,
            'subtotal': subtotal, 'with_30_percent_contingency': subtotal*Decimal('1.3')}.items()},
        'charges_measured': False, 'free_trial_credit_remaining_verified': False,
        'scope': 'Retained disks are billed after VM STOP. A contingency is not a spend cap. Actual quota, trial credit, pricing and excluded services must be checked before an approved apply.'}


def main():
    """Print a finite local estimate; this program never calls a Google API or creates resources."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--running-hours', type=Decimal, default=Decimal(8))
    parser.add_argument('--retained-hours', type=Decimal, default=Decimal(24))
    parser.add_argument('--profile', choices=['basic','standard','accelerated'], default='basic')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    try:
        result = estimate(args.running_hours,args.retained_hours,args.profile)
    except ValueError as error:
        parser.error(str(error))
    text = json.dumps(result, indent=2)+'\n'
    if args.report:
        if args.report.exists():
            parser.error('Preserve the existing estimate and choose a new report path')
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(text,encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
