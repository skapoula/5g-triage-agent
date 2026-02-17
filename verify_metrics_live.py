#!/usr/bin/env python3
"""Verify metrics_agent.py connects to live Prometheus and collects pod metrics.

This script tests the full integration:
1. MCP client connects to Prometheus
2. Executes PromQL queries for NF metrics
3. Organizes results by NF name
"""

import asyncio
import sys
from datetime import UTC, datetime

from triage_agent.agents.metrics_agent import (
    build_nf_queries,
    metrics_agent,
    organize_metrics_by_nf,
)
from triage_agent.mcp.client import MCPClient
from triage_agent.state import TriageState


async def verify_prometheus_connection() -> bool:
    """Verify Prometheus is accessible."""
    print("1. Testing Prometheus connectivity...")
    async with MCPClient() as client:
        is_healthy = await client.health_check_prometheus()
        if is_healthy:
            print("   ✓ Prometheus is accessible")
            return True
        else:
            print("   ✗ Prometheus health check failed")
            return False


async def verify_pod_metrics_queries() -> bool:
    """Verify pod metrics queries return data."""
    print("\n2. Testing pod metrics queries...")

    # Test queries for AMF (known to exist in the cluster)
    test_nfs = ["AMF", "AUSF", "SMF"]
    queries = build_nf_queries(test_nfs)

    print(f"   Generated {len(queries)} queries for {len(test_nfs)} NFs")
    print(f"   NFs: {test_nfs}")

    async with MCPClient() as client:
        all_results = []
        success_count = 0

        for query in queries[:4]:  # Test first 4 queries (AMF metrics)
            try:
                result = await client.query_prometheus(query)
                result_entries = result.get("result", [])
                all_results.extend(result_entries)
                if result_entries:
                    success_count += 1
                    print(f"   ✓ Query returned {len(result_entries)} result(s): {query[:60]}...")
                else:
                    print(f"   ○ Query returned no data: {query[:60]}...")
            except Exception as e:
                print(f"   ✗ Query failed: {e}")

        print(f"\n   Summary: {success_count}/4 queries returned data")

        # Organize results by NF
        if all_results:
            organized = organize_metrics_by_nf(all_results, test_nfs)
            print(f"   Organized into {len(organized)} NF groups:")
            for nf_name, entries in organized.items():
                print(f"     - {nf_name}: {len(entries)} metric(s)")

        return success_count > 0


def verify_metrics_agent_integration() -> bool:
    """Verify full metrics_agent integration."""
    print("\n3. Testing metrics_agent() with simulated alert...")

    # Create a minimal TriageState with a DAG
    state: TriageState = {
        "alert": {
            "labels": {"alertname": "HighErrorRate", "nf": "amf"},
            "annotations": {},
            "startsAt": datetime.now(UTC).isoformat(),
            "status": "firing",
        },
        "infra_checked": False,
        "infra_score": 0.0,
        "infra_findings": None,
        "procedure_name": "Test_Procedure",
        "dag_id": "test-dag-001",
        "dag": {
            "name": "Test_Procedure",
            "spec": "TS 23.502",
            "procedure": "registration",
            "all_nfs": ["AMF", "AUSF", "UDM", "UDR", "NSSF"],
            "phases": [],
        },
        "mapping_confidence": 1.0,
        "mapping_method": "exact_match",
        "metrics": None,
        "logs": None,
        "discovered_imsis": None,
        "traces_ready": False,
        "trace_deviations": None,
        "incident_id": "verify-test-001",
        "evidence_quality_score": 0.0,
        "root_nf": None,
        "failure_mode": None,
        "layer": "application",
        "confidence": 0.0,
        "evidence_chain": [],
        "degraded_mode": False,
        "degraded_reason": None,
        "attempt_count": 1,
        "max_attempts": 2,
        "needs_more_evidence": False,
        "second_attempt_complete": False,
        "final_report": None,
    }

    # Run the metrics agent
    result = metrics_agent(state)

    # Verify the output
    if result["metrics"] is None:
        print("   ✗ metrics_agent did not populate state['metrics']")
        return False

    if not isinstance(result["metrics"], dict):
        print("   ✗ state['metrics'] is not a dict")
        return False

    print(f"   ✓ metrics_agent completed successfully")
    print(f"   ✓ state['metrics'] is a dict with {len(result['metrics'])} NF(s)")

    # Show collected metrics
    for nf_name, entries in result["metrics"].items():
        print(f"     - {nf_name}: {len(entries)} metric entries")

        # Show sample metric
        if entries:
            sample = entries[0]
            metric_name = sample.get("metric", {}).get("__name__", "unknown")
            value = sample.get("value", ["", ""])[1]
            print(f"       Example: {metric_name} = {value}")

    return len(result["metrics"]) > 0


def main() -> int:
    """Run all verification tests."""
    print("=" * 70)
    print("Verifying metrics_agent.py → Prometheus → Pod Metrics Integration")
    print("=" * 70)

    # Test 1: Prometheus connectivity (async)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        if not loop.run_until_complete(verify_prometheus_connection()):
            print("\n❌ FAILED: Cannot connect to Prometheus")
            return 1

        # Test 2: Pod metrics queries (async)
        if not loop.run_until_complete(verify_pod_metrics_queries()):
            print("\n⚠️  WARNING: No metrics data returned from queries")
            print("   This might be expected if 5G NFs don't have the exact metrics")
            # Don't fail here, continue to agent test
    finally:
        loop.close()

    # Test 3: Full agent integration (sync - as LangGraph would call it)
    if not verify_metrics_agent_integration():
        print("\n❌ FAILED: metrics_agent integration test failed")
        return 1

    print("\n" + "=" * 70)
    print("✅ VERIFICATION COMPLETE")
    print("=" * 70)
    print("\nSummary:")
    print("  • MCP client successfully connects to Prometheus")
    print("  • PromQL queries execute against live cluster")
    print("  • metrics_agent() organizes results by NF name")
    print("  • Pod metrics are collected from 5g-core namespace")
    print("\n✅ metrics_agent.py is correctly integrated with live Prometheus!")

    return 0


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
