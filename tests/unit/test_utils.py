from triage_agent.utils import parse_timestamp, extract_log_level, parse_loki_response


def test_parse_timestamp_utc():
    ts = "2024-01-01T12:00:00Z"
    result = parse_timestamp(ts)
    assert isinstance(result, float)
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
