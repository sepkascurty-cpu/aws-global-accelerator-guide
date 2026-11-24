#!/usr/bin/env python3
"""AWS Global Accelerator inspector + cost estimator.

Read-only by default: lists accelerators, listeners, endpoint groups and
endpoint health. Nothing is created or modified unless you pass --create-demo,
which only *prints* the CLI commands (dry run).

Usage:
    pip install boto3
    python global_accelerator_demo.py --list
    python global_accelerator_demo.py --health <accelerator-arn>
    python global_accelerator_demo.py --estimate --endpoints 4 --gb 500
    python global_accelerator_demo.py --create-demo --region us-east-1

Requires AWS credentials with globalaccelerator:List* / Describe* permissions.
Global Accelerator is a GLOBAL service — there is no per-region endpoint.
"""

import argparse
import sys

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError:  # pragma: no cover
    print("boto3 is not installed. Run: pip install boto3")
    sys.exit(1)

# Pricing as of Nov 2026 (us-east-1, Standard accelerator). Verify before budgeting.
HOURLY_FEE_USD = 0.025          # per accelerator, per hour
DT_PREMIUM_LOW_USD = 0.015      # per GB, North America / Europe edge
DT_PREMIUM_HIGH_USD = 0.035     # per GB, other edge locations
HOURS_PER_MONTH = 730


def get_client():
    """Global Accelerator has a single global endpoint (us-west-2)."""
    return boto3.client("globalaccelerator", region_name="us-west-2")


def list_accelerators(client):
    """Print every accelerator with its static IPs and status."""
    paginator = client.get_paginator("list_accelerators")
    found = False
    for page in paginator.paginate():
        for acc in page["Accelerators"]:
            found = True
            # IpSets is a list of dicts: {"IpAddresses": [...], "IpFamily": "IPv4"}
            flat_ips = []
            for ip_set in acc.get("IpSets", []):
                flat_ips.extend(ip_set.get("IpAddresses", []))
            print(f"\n{acc['Name']}")
            print(f"  ARN:        {acc['AcceleratorArn']}")
            print(f"  Status:     {acc['Status']}  (Enabled: {acc['Enabled']})")
            print(f"  Static IPs: {', '.join(flat_ips) or '(none yet)'}")
    if not found:
        print("No accelerators found in this account.")


def describe_health(client, accelerator_arn):
    """Show listeners, endpoint groups, traffic dials and endpoint health."""
    listeners = client.list_listeners(AcceleratorArn=accelerator_arn)["Listeners"]
    if not listeners:
        print("No listeners on this accelerator.")
        return
    for listener in listeners:
        print(f"\nListener {listener['ListenerArn'].split('/')[-1]}")
        print(f"  Protocol: {listener['Protocol']}  Ports: "
              f"{[(p['FromPort'], p['ToPort']) for p in listener['PortRanges']]}")
        for group in client.list_endpoint_groups(
                ListenerArn=listener["ListenerArn"])["EndpointGroups"]:
            print(f"  Endpoint group {group['EndpointGroupRegion']}:")
            print(f"    Traffic dial: {group['TrafficDialPercentage']}%")
            print(f"    Health check: {group['HealthCheckProtocol']} "
                  f"{group['HealthCheckPath']} every "
                  f"{group['HealthCheckIntervalSeconds']}s, "
                  f"threshold {group['ThresholdCount']}")
            for ep in group.get("EndpointDescriptions", []):
                state = ep.get("HealthState", "unknown")
                weight = ep.get("Weight", 128)
                print(f"    - {ep['EndpointId']}  weight={weight}  health={state}")


def estimate(endpoints, gb_per_month, premium=DT_PREMIUM_LOW_USD):
    """Rough monthly cost for a Standard accelerator."""
    fixed = HOURLY_FEE_USD * HOURS_PER_MONTH
    premium_cost = gb_per_month * premium
    print(f"\nMonthly estimate (Standard accelerator, {gb_per_month} GB via GA):")
    print(f"  Accelerator fee:      ${fixed:8.2f}  (${HOURLY_FEE_USD}/hr x {HOURS_PER_MONTH}h)")
    print(f"  Data-transfer premium: ${premium_cost:8.2f}  ({gb_per_month} GB x ${premium}/GB)")
    print(f"  TOTAL (GA only):       ${fixed + premium_cost:8.2f}")
    print("  Note: normal EC2 / ALB / data-transfer charges still apply on top.")
    if gb_per_month > 1000:
        print("  Tip: at this volume, compare against Route 53 latency routing —")
        print("  the premium is only worth it if the latency win is measurable.")


def print_create_demo(region):
    """Print the CLI commands to build a two-region accelerator (dry run)."""
    print(f"""
# Dry run — copy/paste these to build the accelerator (region: {region})
aws globalaccelerator create-accelerator --name my-app-accelerator \\
  --ip-address-type IPV4 --enabled

aws globalaccelerator create-listener --accelerator-arn $ACCEL_ARN \\
  --protocol TCP --port-ranges FromPort=443,ToPort=443

# Primary region — full traffic
aws globalaccelerator create-endpoint-group --listener-arn $LISTENER_ARN \\
  --endpoint-group-region us-east-1 --traffic-dial-percentage 100 \\
  --endpoint-configurations EndpointId=$ALB_ARN_USE1,Weight=128

# Standby region — dial 0 until failover
aws globalaccelerator create-endpoint-group --listener-arn $LISTENER_ARN \\
  --endpoint-group-region eu-west-1 --traffic-dial-percentage 0 \\
  --endpoint-configurations EndpointId=$ALB_ARN_EUW1,Weight=128
""")


def main():
    parser = argparse.ArgumentParser(description="Global Accelerator inspector")
    parser.add_argument("--list", action="store_true", help="list accelerators")
    parser.add_argument("--health", metavar="ARN", help="show endpoint health")
    parser.add_argument("--estimate", action="store_true", help="cost estimate")
    parser.add_argument("--endpoints", type=int, default=2)
    parser.add_argument("--gb", type=int, default=500, help="GB/month via GA")
    parser.add_argument("--create-demo", action="store_true", help="print CLI dry run")
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()

    if args.create_demo:
        print_create_demo(args.region)
        return
    if args.estimate:
        estimate(args.endpoints, args.gb)
        return

    try:
        client = get_client()
    except (BotoCoreError, ClientError) as e:
        print(f"Could not create Global Accelerator client: {e}")
        sys.exit(1)

    if args.health:
        try:
            describe_health(client, args.health)
        except ClientError as e:
            print(f"Error: {e}")
            sys.exit(1)
    else:
        try:
            list_accelerators(client)
        except ClientError as e:
            print(f"Error: {e}")
            sys.exit(1)


if __name__ == "__main__":
    main()
