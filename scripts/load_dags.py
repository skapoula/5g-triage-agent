#!/usr/bin/env python3
"""Load DAGs into Memgraph using Python driver."""

from pathlib import Path

from neo4j import GraphDatabase

MEMGRAPH_URI = "bolt://localhost:7687"
DAG_DIR = Path(__file__).parent.parent / "dags"

def main():
    print(f"Connecting to Memgraph at {MEMGRAPH_URI}")
    driver = GraphDatabase.driver(MEMGRAPH_URI)

    with driver.session() as session:
        # Test connection
        result = session.run("RETURN 'Connected' AS status")
        print(f"✓ {result.single()['status']}")

        # Load each .cypher file
        for cypher_file in sorted(DAG_DIR.glob("*.cypher")):
            print(f"Loading: {cypher_file.name}")
            cypher_content = cypher_file.read_text()

            # Split by semicolons and execute each statement
            statements = [s.strip() for s in cypher_content.split(";") if s.strip()]
            for stmt in statements:
                if stmt:
                    session.run(stmt)
            print("  ✓ Loaded")

        # Verify
        print("\nLoaded DAGs:")
        result = session.run("MATCH (t:ReferenceTrace) RETURN t.name AS dag, t.spec AS spec")
        for record in result:
            print(f"  - {record['dag']}: {record['spec']}")

    driver.close()
    print("\n✓ DAG loading complete!")

if __name__ == "__main__":
    main()
