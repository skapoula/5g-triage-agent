import json
import time
from pathlib import Path

from triage_agent.utils import (
    count_tokens,
    extract_log_level,
    parse_loki_response,
    parse_timestamp,
    save_artifact,
)


def test_parse_timestamp_utc():
    ts = "2024-01-01T12:00:00Z"
    result = parse_timestamp(ts)
    assert result == 1704110400.0


def test_parse_timestamp_with_offset():
    ts = "2024-01-01T13:00:00+01:00"
    result = parse_timestamp(ts)
    assert result == 1704110400.0


def test_extract_log_level_fatal():
    assert extract_log_level("FATAL: core dump") == "FATAL"


def test_extract_log_level_default():
    assert extract_log_level("no level here") == "INFO"


def test_parse_loki_response_empty():
    assert parse_loki_response({}) == []


def test_parse_loki_response_basic():
    data = {
        "data": {
            "result": [
                {
                    "stream": {"k8s_pod_name": "amf-abc"},
                    "values": [["1700000000123456789", "ERROR something"]],
                }
            ]
        }
    }
    logs = parse_loki_response(data)
    assert len(logs) == 1
    assert logs[0]["pod"] == "amf-abc"
    assert logs[0]["level"] == "ERROR"
    assert logs[0]["timestamp"] == 1700000000


def test_extract_log_level_mixed_case():
    assert extract_log_level("error: dial timeout") == "ERROR"


def test_extract_log_level_warn():
    assert extract_log_level("WARNING: retrying") == "WARN"


def test_extract_log_level_debug():
    assert extract_log_level("debug trace enabled") == "DEBUG"


def test_parse_loki_response_pod_label_fallback():
    """When k8s_pod_name absent, falls back to pod label."""
    data = {
        "data": {
            "result": [
                {
                    "stream": {"pod": "smf-xyz"},
                    "values": [["1700000001000000000", "INFO connected"]],
                }
            ]
        }
    }
    logs = parse_loki_response(data)
    assert logs[0]["pod"] == "smf-xyz"


def test_parse_loki_response_multiple_streams():
    data = {
        "data": {
            "result": [
                {
                    "stream": {"k8s_pod_name": "amf-a"},
                    "values": [
                        ["1700000001000000000", "ERROR one"],
                        ["1700000002000000000", "WARN two"],
                    ],
                },
                {
                    "stream": {"k8s_pod_name": "smf-b"},
                    "values": [["1700000003000000000", "INFO three"]],
                },
            ]
        }
    }
    logs = parse_loki_response(data)
    assert len(logs) == 3
    assert logs[0]["pod"] == "amf-a"
    assert logs[2]["pod"] == "smf-b"


# ---------------------------------------------------------------------------
# count_tokens
# ---------------------------------------------------------------------------


def test_count_tokens_empty_string_returns_one():
    assert count_tokens("") == 1


def test_count_tokens_400_chars_returns_100():
    assert count_tokens("a" * 400) == 100


def test_count_tokens_four_char_boundary():
    assert count_tokens("abcd") == 1


def test_count_tokens_eight_chars_returns_two():
    assert count_tokens("abcdefgh") == 2


# ---------------------------------------------------------------------------
# save_artifact
# ---------------------------------------------------------------------------


def test_save_artifact_writes_json_file(tmp_path: Path) -> None:
    """save_artifact should write valid JSON to artifacts_dir/incident_id/name."""
    data = {"key": "value", "count": 42}
    save_artifact("inc-001", "test.json", data, str(tmp_path))

    # The write is asynchronous; wait briefly for the background thread
    deadline = time.monotonic() + 3.0
    target = tmp_path / "inc-001" / "test.json"
    while not target.exists() and time.monotonic() < deadline:
        time.sleep(0.05)

    assert target.exists(), "Artifact file was not written"
    assert json.loads(target.read_text()) == data


def test_save_artifact_creates_incident_dir(tmp_path: Path) -> None:
    """save_artifact must create the incident subdirectory if absent."""
    save_artifact("new-incident", "snap.json", {"x": 1}, str(tmp_path))

    deadline = time.monotonic() + 3.0
    target = tmp_path / "new-incident" / "snap.json"
    while not target.exists() and time.monotonic() < deadline:
        time.sleep(0.05)

    assert (tmp_path / "new-incident").is_dir()


def test_save_artifact_does_not_raise_on_bad_path() -> None:
    """save_artifact must silently swallow errors — never raise."""
    # Use an invalid path (root-owned directory) — should not raise
    save_artifact("inc", "x.json", {"a": 1}, "/proc/triage_test_nonexistent")
    # Give background thread time to attempt and fail
    time.sleep(0.1)


from triage_agent.utils import compress_dag, compress_trace_deviations


class TestCompressDag:
    def test_returns_empty_for_none(self) -> None:
        assert compress_dag(None, 1000) == []

    def test_returns_as_is_when_within_budget(self) -> None:
        dags = [{"phases": [{"order": 1, "keywords": ["k"], "success_log": "ok"}]}]
        result = compress_dag(dags, 10_000)
        assert result == dags

    def test_strips_keywords_and_success_log_when_over_budget(self) -> None:
        phase = {"order": 1, "keywords": ["k"] * 500, "success_log": "ok", "failure_patterns": ["*fail*"]}
        dags = [{"phases": [phase]}]
        result = compress_dag(dags, 50)
        assert "keywords" not in result[0]["phases"][0]
        assert "success_log" not in result[0]["phases"][0]

    def test_all_zero_phases_returns_stable_result(self) -> None:
        """compress_dag must not raise or loop infinitely when all phases are empty after step 3."""
        dags = [{"phases": []} for _ in range(20)]
        result = compress_dag(dags, 1)
        assert isinstance(result, list)


class TestCompressTraceDeviations:
    def test_returns_empty_for_none(self) -> None:
        assert compress_trace_deviations(None, 1000) == {}

    def test_slices_per_dag_to_max(self) -> None:
        devs = {"dag_a": [{"d": i} for i in range(10)]}
        result = compress_trace_deviations(devs, 10_000)
        assert len(result["dag_a"]) <= 3   # default rca_max_deviations_per_dag
