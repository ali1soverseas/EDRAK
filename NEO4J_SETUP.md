# Edrak – Neo4j Local Setup Guide for Teammates

This guide sets up a local Neo4j database with Docker and loads Edrak's sample data with `loader.py`.

You should see `connection.py`, `loader.py`, `schema.cypher`, `sample_data.json` and the `ingestion` and `retrieval` folders.

## 1. Start Neo4j with Docker

```powershell
docker run -d `
  --name edrak-neo4j `
  -p 7474:7474 `
  -p 7687:7687 `
  -e NEO4J_AUTH=neo4j/edrak-password `
  -v edrak-neo4j-data:/data `
  neo4j:5
```

This creates and starts a Neo4j container on your computer. The first start takes about 20–30 seconds. Wait until `docker logs edrak-neo4j` shows `Started.`

| Setting          | Value              |
| ---------------- | ------------------ |
| Container name   | `edrak-neo4j`      |
| Username         | `neo4j`            |
| Example password | `edrak-password`   |
| Database name    | `neo4j`            |
| Browser port     | `7474`             |
| Bolt port        | `7687`             |
| Docker volume    | `edrak-neo4j-data` |

Everyone can use the same container and volume names, because Docker runs separately on each computer. Your password can differ from your teammates', as long as it matches your `.env`.

> **The password is set only once.** `NEO4J_AUTH` is applied only the first time the volume is created. If you later run `docker run` with a different password but the same volume, the old password still applies.

### If the container already exists

Don't run `docker run` again. Start the existing container:

```powershell
docker start edrak-neo4j
docker ps
```

## 2. Set up the Python environment
From the repository root, install the project requirements and set the connection values
in the root `.env`: `GRAPH_ENABLED=true`, `NEO4J_URI=bolt://localhost:7687`,
`NEO4J_USER=neo4j`, and `NEO4J_PASSWORD` to the password for your container. Do not
commit the `.env` file.

Run the loader from the repository root:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt
$env:PYTHONPATH = "backend\src"
.\venv\Scripts\python.exe -m edrak.graph.loader --apply-schema
```

The loader:

1. Reads `edrak/graph/sample_data.json` and validates every request, plan, result and verdict against Edrak's contract classes. A bad file stops here, before connecting.
2. Connects to Neo4j using your `.env`.
3. Applies the schema: constraints, indexes and the four `Worker` nodes.
4. Loads the catalog and both runs (`req-nvidia-001`, `req-nvidia-002`) through the normal ingestion pipeline.
5. Prints node counts and a few demo queries.


Running the loader twice is safe: every write is a `MERGE` on a stable id, so nothing is duplicated.

> **The sample data is test data.** It compares NVIDIA with Broadcom, Intel Corp and AMD. Some figures are close to public reports, but the sources, posts and several claims are invented. It loads into the `sandbox` workspace, so it never mixes with a real workspace's research. Don't use `--workspace` to load it into a real one.

## 3. Verify the database

### A. Open Neo4j Browser

Go to <http://localhost:7474> and log in:

| Field    | Value                    |
| -------- | ------------------------ |
| Username | `neo4j`                  |
| Password | your configured password |

### B. Count nodes and relationships

```cypher
MATCH (n) RETURN count(n) AS total_nodes;
```

```cypher
MATCH ()-[r]->() RETURN count(r) AS total_relationships;
```

### C. Check node labels

```cypher
MATCH (n)
UNWIND labels(n) AS label
RETURN label, count(*) AS count
ORDER BY count DESC;
```

You should see three groups of labels:

- **Pipeline:** `BusinessRequest`, `ResearchPlan`, `ResearchTask`, `WorkerResult`, `Finding`, `Evidence`, `Source`, `VerificationResult`, `CrossSignal`, `DecisionAnalysis`, `Opportunity`, `Risk`, `RecommendedAction`, `DecisionOption`, `Recommendation`
- **Entities:** `Company`, `Product`, `Feature`, `PricingPlan`, `Market`, `Regulation`, `Theme`, `Topic`, `CustomerSegment`, `Platform`
- **Supporting:** `Worker`, `Workspace`, `Metric`, `MetricObservation`, `MetricRequirement`, `InformationGap`, `Conflict`, `Document`

### D. Spot checks

The two runs:

```cypher
MATCH (r:BusinessRequest) RETURN r.request_id, r.status ORDER BY r.request_id;
```

The four companies (NVIDIA, Broadcom, Intel Corp, AMD) and who competes with whom:

```cypher
MATCH (n:Company {id: 'nvidia'})-[e:COMPETES_WITH]->(c:Company)
RETURN c.name, size(e.finding_ids) AS supporting_findings;
```

Metric history. This should return 288 GB (MI355X) from run 1 and 432 GB (MI400) from run 2:

```cypher
MATCH (o:MetricObservation {metric_id: 'hbm_capacity_gb', entity_id: 'amd'})
RETURN o.observed_at, o.value_text, o.label, o.request_id
ORDER BY o.observed_at;
```

One recommendation traced back to its sources:

```cypher
MATCH p = (:BusinessRequest {request_id: 'req-nvidia-001'})-[:HAS_ANALYSIS]->(:DecisionAnalysis)
          -[:RECOMMENDS]->(:RecommendedAction)-[:SUPPORTED_BY_SIGNAL]->(:CrossSignal)
          -[:BASED_ON]->(:Finding)-[:SUPPORTED_BY]->(:Evidence)
RETURN p;
```

