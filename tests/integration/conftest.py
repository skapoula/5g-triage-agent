"""pytest configuration for integration tests."""


def pytest_addoption(parser: object) -> None:
    """Register --memgraph-url option for integration tests."""
    parser.addoption(  # type: ignore[attr-defined]
        "--memgraph-url",
        action="store",
        default="bolt://localhost:7687",
        help="Memgraph Bolt URL for integration tests",
    )
