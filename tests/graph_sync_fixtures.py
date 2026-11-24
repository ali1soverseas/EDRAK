"""A stand-in for the knowledge graph connection, shared by the company fact sync tests."""


class FakeGraph:
    """Stands in for GraphConnection: records writes, and can be switched off or made to fail."""

    def __init__(self, available=True, fail_on=None):
        self.is_available = available
        self.fail_on = fail_on  # a substring of the query that should raise
        self.calls = []
        self.schema_applied = 0
        self.available_checks = 0

    def available(self):
        self.available_checks += 1
        return self.is_available

    def apply_schema(self):
        self.schema_applied += 1

    def write(self, query, **params):
        if self.fail_on and self.fail_on in query:
            raise RuntimeError("neo4j said no")
        self.calls.append((query, params))

    def write_batches(self, query, rows, **params):
        self.write(query, rows=list(rows), **params)

    # what the writes amount to
    def published(self):
        return [r["props"]["fact_id"] for q, p in self.calls if "MERGE (f:CompanyFact" in q for r in p["rows"]]

    def retracted(self):
        return [i for q, p in self.calls if "DETACH DELETE f" in q for i in p["node_ids"]]
