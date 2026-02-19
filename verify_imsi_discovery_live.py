#!/usr/bin/env python3
"""Verify live IMSI discovery against Loki.

Tests the full UeTracesAgent IMSI pipeline against a real Loki instance:
  1. Loki connectivity (MCP health check + direct HTTP fallback)
  2. IMSI discovery pass — scan recent logs for 'imsi-' pattern
  3. Per-IMSI trace construction — fetch full procedure logs per IMSI
  4. Trace contraction — structure events chronologically

Usage:
    python verify_imsi_discovery_live.py [--lookback SECONDS]

    --lookback   How many seconds of Loki history to scan (default: 300)

Exit codes:
    0  Loki reachable (even if 0 IMSIs found — that is a valid cluster state)
    1  Loki unreachable
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime

from triage_agent.agents.ue_traces_agent import (
    contract_imsi_trace,
    extract_unique_imsis,
    loki_query,
    per_imsi_logql,
)
from triage_agent.config import get_config
from triage_agent.mcp.client import MCPClient


# ---------------------------------------------------------------------------
# Step 1: connectivity
# ---------------------------------------------------------------------------


async def check_loki_mcp() -> bool:
    """Probe Loki /ready via MCP client."""
    async with MCPClient() as client:
        return await client.health_check_loki()


async def check_loki_direct() -> bool:
    """Probe Loki /ready via direct HTTP (bypasses MCP)."""
    import httpx

    cfg = get_config()
    try:
        async with httpx.AsyncClient(timeout=cfg.mcp_timeout) as client:
            r = await client.get(f"{cfg.loki_url}/ready")
            return r.status_code == 200
    except Exception:
        return False


def verify_loki_connectivity() -> bool:
    """Check Loki is reachable via at least one path."""
    print("1. Testing Loki connectivity...")
    loop = asyncio.new_event_loop()
    try:
        mcp_ok = loop.run_until_complete(check_loki_mcp())
        direct_ok = loop.run_until_complete(check_loki_direct())
    finally:
        loop.close()

    if mcp_ok:
        print("   ✓ Loki reachable via MCP")
    else:
        print("   ○ MCP path unavailable")

    if direct_ok:
        print("   ✓ Loki reachable via direct HTTP")
    else:
        print("   ○ Direct HTTP path unavailable")

    if not mcp_ok and not direct_ok:
        print("   ✗ Loki unreachable on both paths")
        return False

    return True


# ---------------------------------------------------------------------------
# Step 2: IMSI discovery pass
# ---------------------------------------------------------------------------


def run_imsi_discovery(now: int, lookback: int) -> tuple[list[str], list[dict]]:
    """Run the discovery LogQL query and extract unique IMSIs.

    Returns (imsi_list, raw_discovery_logs).
    """
    cfg = get_config()
    start = now - lookback
    end = now

    logql = f'{{k8s_namespace_name="{cfg.core_namespace}"}} |~ "(?i)imsi-"'
    print(f"\n2. Running IMSI discovery query...")
    print(f"   Namespace : {cfg.core_namespace}")
    print(f"   Window    : {lookback}s  ({datetime.fromtimestamp(start, UTC).isoformat()} → now)")
    print(f"   LogQL     : {logql}")

    logs = loki_query(logql, start=start, end=end)
    imsis = extract_unique_imsis(logs)

    print(f"   Log entries returned : {len(logs)}")
    print(f"   Unique IMSIs found   : {len(imsis)}")
    if imsis:
        for imsi in imsis:
            print(f"     • {imsi}")

    return imsis, logs


# ---------------------------------------------------------------------------
# Step 3: per-IMSI trace construction
# ---------------------------------------------------------------------------


def run_per_imsi_traces(imsis: list[str], now: int) -> list[dict]:
    """Fetch and contract a trace for each discovered IMSI.

    Uses imsi_trace_lookback_seconds (default 120s) for trace window,
    matching the production UeTracesAgent behaviour.
    """
    cfg = get_config()
    traces = []

    print(f"\n3. Building per-IMSI traces ({len(imsis)} IMSI(s))...")
    if not imsis:
        print("   (no IMSIs to trace)")
        return traces

    for imsi in imsis:
        logql = per_imsi_logql(imsi)
        start = now - cfg.imsi_trace_lookback_seconds
        end = now + cfg.alert_lookahead_seconds

        raw = loki_query(logql, start=start, end=end)
        trace = contract_imsi_trace(raw, imsi)
        traces.append(trace)

        print(f"   IMSI {imsi}: {len(raw)} log entries → {len(trace['events'])} events")
        for ev in trace["events"][:3]:
            ts = datetime.fromtimestamp(ev["timestamp"], UTC).strftime("%H:%M:%S")
            print(f"     [{ts}] {ev['nf']:6s}  {ev['message'][:72]}")
        if len(trace["events"]) > 3:
            print(f"     ... ({len(trace['events']) - 3} more events)")

    return traces


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Live IMSI discovery verification")
    parser.add_argument(
        "--lookback",
        type=int,
        default=300,
        help="Seconds of Loki history to scan for IMSIs (default: 300)",
    )
    args = parser.parse_args()

    print("=" * 70)
    print("Live IMSI Discovery Verification  —  UeTracesAgent → Loki")
    print("=" * 70)

    # Step 1: connectivity
    if not verify_loki_connectivity():
        print("\n❌ FAILED: Cannot reach Loki")
        return 1

    now = int(datetime.now(UTC).timestamp())

    # Step 2: discovery pass
    imsis, _discovery_logs = run_imsi_discovery(now, lookback=args.lookback)

    # Step 3: per-IMSI traces
    traces = run_per_imsi_traces(imsis, now)

    # Summary
    print("\n" + "=" * 70)
    total_events = sum(len(t["events"]) for t in traces)
    print(f"IMSI discovery   : {len(imsis)} IMSI(s) found in last {args.lookback}s")
    print(f"Traces built     : {len(traces)}")
    print(f"Total events     : {total_events}")
    if len(imsis) == 0:
        print("\n✅ VERIFICATION COMPLETE  (Loki reachable; no active IMSIs in window)")
    else:
        print("\n✅ VERIFICATION COMPLETE")
    print("=" * 70)

    return 0


if __name__ == "__main__":
    sys.exit(main())
