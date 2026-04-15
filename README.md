# IAM Misconfiguration Detection Pipeline

Minimal end-to-end implementation of the workflow in `steps.md`:

1. Ingest IAM workbook data (`policies`, `users`, `groups`, `roles`)
2. Normalize/parse policy statements safely
3. Build IAM graph in Neo4j
4. Generate policy embeddings with Node2Vec
5. Build ML dataset (`X`, `y`, metadata)
6. Split for unsupervised anomaly detection
7. Train anomaly detectors
8. Evaluate detector performance
9. Update graph from old/new snapshots

## Project Structure

```text
config/
  data.yaml
  neo4j.yaml
  model.yaml
src/
  core/
    common.py
    data_ops.py
    graph_ops.py
    ml_ops.py
  pipeline.py
outputs/
  logs/
  metrics/
  predictions/
dataset.xlsx
requirements.txt
README.md
```

## Environment Setup

1. Create an environment and install dependencies:

```bash
python -m pip install -r requirements.txt
```

2. Start Neo4j with Graph Data Science enabled (example Docker command):

```bash
docker run -p7474:7474 -p7687:7687 -e NEO4J_AUTH=neo4j/password --env NEO4JLABS_PLUGINS='["graph-data-science"]' neo4j
```

3. Update credentials in `config/neo4j.yaml` if needed.

## Data Schema

The default expected sheets and columns are configured in `config/data.yaml`.
Core required columns:

- `policies`: `PolicyName`, `PolicyId`, `Arn`, `PolicyObject`
- `users`: `UserName`, `UserId`, `Arn`, `AttachedPolicies`
- `groups`: `GroupName`, `GroupId`, `Arn`, `AttachedPolicies`, `Users`
- `roles`: `RoleName`, `RoleId`, `Arn`, `AttachedPolicies`

If your workbook uses different names, update `config/data.yaml`.

## Graph Schema

Node labels:

- `Policy`, `Action`, `NotAction`, `Resource`, `NotResource`, `User`, `Group`, `Role`

Relationship types:

- `CONTAINS`
- `WORKS_ON`
- `WORKS_NOT_ON`
- `IS_ATTACHED_TO`
- `PART_OF`

Graph counts are written to:

- `outputs/logs/graph_counts.json`

## Embedding Method

Node2Vec is run on `Policy`-centered relationships configured in `config/model.yaml`:

- default write property: `embeddingNode2vec`
- default dimension: `128`
- default iterations: `100`
- default walk length: `5000`

Embedding diagnostics are written to:

- `outputs/logs/embedding_report.json`

## Train/Test Protocol

Split logic (`src/core/ml_ops.py`) follows unsupervised anomaly detection:

- normal policies (`y=1`) are split into train/test
- anomalies (`y=-1`) are held for test-time detection
- `seed` and split ratio are fixed via config for reproducibility

Split diagnostics are written to:

- `outputs/logs/split_summary.json`

## Metrics

`src/core/ml_ops.py` computes per-model:

- confusion matrix (`tn`, `fp`, `fn`, `tp`)
- precision, recall, F1
- ROC-AUC and PR-AUC (if score vectors are available)

Outputs:

- `outputs/metrics/model_metrics.csv`
- `outputs/metrics/comparison.md`

## Reproducibility

The pipeline writes a manifest with timestamp, config paths, seed, and enabled stages:

- `outputs/logs/run_manifest.json`

Keep versions pinned via `requirements.txt` and set fixed seeds in `config/model.yaml`.

## How To Run

### Full pipeline

```bash
python -m src.pipeline run --data-config config/data.yaml --neo4j-config config/neo4j.yaml --model-config config/model.yaml
```

### Build graph + embeddings + dataset/split, but skip training

```bash
python -m src.pipeline run --skip-train
```

### Skip embedding stage

```bash
python -m src.pipeline run --skip-embed
```

### Update graph from old/new snapshots

```bash
python -m src.pipeline update --old-data-config config/data.yaml --new-data-config config/new_data.yaml --neo4j-config config/neo4j.yaml --model-config config/model.yaml
```

Start from `config/new_data.example.yaml` and set `dataset_path` for your new snapshot.

## Notes

- Policy parsing failures do not crash the pipeline; they are logged to:
  - `outputs/logs/policy_parse_errors.csv`
- Label vector `y` is built from known misconfiguration names/IDs in `config/data.yaml`.
- Predictions are written per model to:
  - `outputs/predictions/<model>_pred.csv`
