## 1) Environment setup

**Algorithm**

1. Create isolated runtime.
2. Install graph DB client, dataframe tooling, ML tooling.
3. Start graph database with graph-data-science capability.
4. Validate DB connectivity.

---

## 2) Input dataset ingestion

**Algorithm**

1. Read Excel workbook from `collector/example/...xlsx`.
2. Load required sheets: `policies`, `users`, `groups`, `roles`.
3. Standardize missing values.
4. Normalize inconsistent column names.
5. Reconstruct truncated/split policy text fields if needed.

---

## 3) Policy document normalization

**Algorithm**

1. For each policy row, normalize JSON-like text:
   - quote normalization
   - boolean token normalization
2. Parse policy object into structured statements.
3. If parse fails, log and skip row (do not crash pipeline).

---

## 4) Graph schema construction

**Algorithm**

1. Create node types:
   - Policy, Action, NotAction, Resource, NotResource, User, Group, Role
2. Create policy core nodes first.
3. Extract resources from statements and create resource nodes.
4. Extract actions from statements and create action nodes.
5. Connect:
   - Policy → Action (`CONTAINS`)
   - Action → Resource (`WORKS_ON` / `WORKS_NOT_ON`)
6. Attach entities to policies (paper convention: entity → policy):
   - User/Group/Role → Policy (`ATTACHED_TO`)
7. Add group membership:
   - User → Group (`PART_OF`)

---

## 5) Graph quality checks

**Algorithm**

1. Count nodes per label.
2. Count edges per relationship type.
3. Validate no empty graph sections.
4. Spot-check several policy subgraphs manually.

---

## 6) Representation learning (feature generation)

**Algorithm**

1. Project graph with Policy nodes and selected relation types.
2. Run Node2Vec with fixed hyperparameters.
3. Write embedding vector property to each Policy node.
4. Validate embedding exists and has expected dimension.

---

## 7) Dataset assembly for ML

**Algorithm**

1. Pull policy embeddings from graph DB into matrix `X`.
2. Build label vector `y` from known misconfiguration list/rules.
3. Remove invalid/missing-embedding samples.
4. Align sample IDs across `X`, `y`, and metadata.

---

## 8) Train/test split strategy

**Algorithm**

1. Define normal vs anomalous policies.
2. Split data so training is primarily/only normal samples (unsupervised anomaly setting).
3. Keep anomalies for test-time detection.
4. Preserve reproducibility with fixed random seed.

---

## 9) Model training

**Algorithm (per model)**

1. Initialize anomaly detector:
   - Isolation Forest / LOF / One-Class SVM / Elliptic Envelope
2. Fit on training embeddings.
3. Generate anomaly scores/predictions on test set.
4. Convert detector outputs to consistent binary label format.

---

## 10) Evaluation

**Algorithm**

1. Compute confusion matrix.
2. Compute precision, recall, F1, and optionally ROC-AUC/PR-AUC.
3. Compare metrics across all detectors.
4. Record top detected anomalies for qualitative inspection.

---

## 11) Baseline comparison (optional)

**Algorithm**

1. Run rule-based baseline (Cloud Custodian style checks) on same input.
2. Map baseline outputs to same label space.
3. Compare precision/recall tradeoffs against anomaly models.

---

## 12) Update pipeline (temporal data)

**Algorithm**

1. Load old and new snapshots.
2. Identify deleted/added/modified policies by key (`PolicyName`, `PolicyId`).
3. For metadata-only changes: update node properties.
4. For policy-document changes: rebuild affected policy subgraph.
5. Refresh principal entities and attachments.
6. Recompute embeddings and rerun evaluation.

---

## 13) Reproducibility protocol

**Algorithm**

1. Fix versions of dependencies.
2. Fix random seeds for all models/splits.
3. Log config + hyperparameters + data snapshot hash.
4. Save metrics and prediction artifacts per run.
5. Repeat runs and report mean/std where applicable.

---

If you want, I can convert this into a **one-page project template** (section headers + what to implement in each file: `ingest.py`, `graph_build.py`, `embed.py`, `train.py`, `eval.py`).

---

---

---

## Project structure (recommended)

```text
my-misdet-replication/
├─ config/
│  ├─ data.yaml
│  ├─ neo4j.yaml
│  └─ model.yaml
├─ data/
│  ├─ raw/
│  │  └─ iam_policy_data_2021-03-26_14:11.xlsx
│  ├─ interim/
│  └─ processed/
├─ src/
│  ├─ ingest.py
│  ├─ normalize.py
│  ├─ graph_build.py
│  ├─ embed.py
│  ├─ dataset.py
│  ├─ split.py
│  ├─ train.py
│  ├─ evaluate.py
│  ├─ update_graph.py
│  └─ pipeline.py
├─ outputs/
│  ├─ logs/
│  ├─ metrics/
│  └─ predictions/
└─ README.md
```

---

## 1) `ingest.py` — read dataset

**Implement**

1. Load Excel workbook.
2. Read sheets: `policies`, `users`, `groups`, `roles`.
3. Validate required columns exist per sheet.
4. Return dataframes + schema report.

**Output**

- In-memory tables and optional schema JSON to `outputs/logs/schema_report.json`.

---

## 2) `normalize.py` — clean/repair policy text

**Implement**

1. Fill nulls consistently.
2. Normalize role column names.
3. Reconstruct split policy text (e.g., `ExtraPolicySpace`).
4. Normalize JSON-like fields (quotes, booleans).
5. Parse statements; tag parse failures.

**Output**

- Cleaned dataframes
- Parse error log (`outputs/logs/policy_parse_errors.csv`)

---

## 3) `graph_build.py` — create graph in Neo4j

**Implement**

1. Connect to Neo4j.
2. Optionally clear graph.
3. Create nodes:
   - Policy, Resource, NotResource, Action, NotAction, User, Group, Role
4. Create relationships:
   - `CONTAINS`, `WORKS_ON`, `WORKS_NOT_ON`, `IS_ATTACHED_TO`, `PART_OF`
5. Use batching for performance.
6. Track inserted counts.

**Output**

- Graph population summary in `outputs/logs/graph_counts.json`.

---

## 4) `embed.py` — generate graph embeddings

**Implement**

1. Run Node2Vec over Policy graph projection.
2. Write embedding property (e.g., `embeddingNode2vec`) to Policy nodes.
3. Validate vector dimensionality and coverage ratio.

**Output**

- Embedding diagnostics in `outputs/logs/embedding_report.json`.

---

## 5) `dataset.py` — build ML matrix

**Implement**

1. Query Policy nodes and embedding vectors.
2. Build feature matrix `X`.
3. Construct labels `y` from misconfiguration definition/list.
4. Keep policy IDs/names as metadata.
5. Drop invalid records.

**Output**

- `X`, `y`, metadata table
- Dataset summary file (`outputs/logs/dataset_summary.json`)

---

## 6) `split.py` — train/test strategy

**Implement**

1. Reproducible seed-based split.
2. Unsupervised mode:
   - train mostly/only normal samples
   - anomalies held for test
3. Save split indices.

**Output**

- `train_idx`, `test_idx`
- `outputs/logs/split_summary.json`

---

## 7) `train.py` — model training

**Implement**

1. Train detectors:
   - Isolation Forest
   - Local Outlier Factor
   - One-Class SVM
   - Elliptic Envelope
2. Keep shared interface:
   - `fit(X_train)`
   - `score/predict(X_test)`
3. Convert outputs to consistent binary anomaly labels.

**Output**

- Per-model predictions and scores:
  - `outputs/predictions/<model>_pred.csv`

---

## 8) `evaluate.py` — metrics + comparison

**Implement**

1. Compute:
   - confusion matrix
   - precision, recall, F1
   - optional ROC-AUC/PR-AUC
2. Generate per-model and comparative report.
3. Rank models by selected metric.

**Output**

- `outputs/metrics/model_metrics.csv`
- `outputs/metrics/comparison.md`

---

## 9) `update_graph.py` — old vs new snapshot updates

**Implement**

1. Load old/new snapshots.
2. Diff by (`PolicyName`, `PolicyId`).
3. Delete removed policies.
4. Add new policies.
5. For changed policy document: rebuild policy subgraph.
6. Refresh entities/attachments.
7. Re-embed graph.

**Output**

- `outputs/logs/update_report.json`

---

## 10) `pipeline.py` — orchestration

**Implement**

1. Run full sequence:
   - ingest → normalize → graph_build → embed → dataset → split → train → evaluate
2. Add stage toggles (`--skip-embed`, etc.).
3. Write run manifest (timestamp, config, seed, versions).

**Output**

- `outputs/logs/run_manifest.json`

---

## Config files (minimal)

### `config/data.yaml`

- dataset path
- required sheets
- required columns
- label source (misconfig list/rules)

### `config/neo4j.yaml`

- bolt URI
- username/password
- clear-graph flag
- embedding hyperparameters

### `config/model.yaml`

- random seed
- enabled models
- each model’s hyperparameters
- evaluation metric priority

---

## Suggested run modes

1. **Smoke test**
   - small sample + one model

2. **Full replication**
   - all data + all four detectors

3. **Your custom model**
   - plug into `train.py` with same split/eval interface for fair comparison

---

## Minimal README sections you should include

1. Objective
2. Data schema
3. Graph schema
4. Embedding method + params
5. Train/test protocol
6. Metrics
7. Reproducibility notes
8. How to run end-to-end
