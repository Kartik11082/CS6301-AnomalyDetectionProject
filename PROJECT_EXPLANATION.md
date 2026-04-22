# IAM Misconfiguration Detection — Pipeline Walkthrough

This document follows `src/pipeline.py` from top to bottom and explains everything the pipeline does at each step, with examples of the data at each stage. The goal is to let a reader visualize the transformations: Excel row → policy statement → graph subgraph → 64-dim vector → anomaly label.

---

## What this project does (in one paragraph)

The pipeline takes an Excel workbook of AWS IAM data (policies, users, groups, roles), parses every policy document into its statements, loads everything into a Neo4j graph (Policy → Action → Resource), learns a fixed-length vector for every Policy node using Node2Vec, and finally runs four unsupervised anomaly detectors on those vectors. Known misconfigured policy names (listed in `config/data.yaml`) are held out from training and used only to measure how well the models rank them against normal policies.

---

## The entrypoint

```bash
python -m src.pipeline run
```

This calls `run_full_pipeline(args)` which does exactly this, in order:

```
1.  prepare output folders
2.  load three YAML configs
3.  ingest + normalize Excel workbook
4.  build the Neo4j graph
5.  run Node2Vec to produce Policy embeddings
6.  build the ML dataset (X, y, metadata) from the graph
7.  split the dataset (train = normals only, test = held-out normals + anomalies)
8.  (optional) grid-search hyperparameters
9.  train the four anomaly detectors and save predictions
10. evaluate and write the comparison report
11. write a run manifest (seed, configs, stages executed)
```

Each step leaves a diagnostic file under `outputs/logs/` so the run is auditable without re-executing anything. There is also an `update` subcommand (covered at the end) that diffs an old workbook against a new one and surgically rebuilds only the policies that changed.

---

## Step 1 — Prepare output folders

```
outputs/logs/         → one JSON per stage (summaries, parse errors, graph counts, embedding report, split summary)
outputs/metrics/      → model_metrics.csv, comparison.md
outputs/predictions/  → per-model CSV with y_true, y_pred, anomaly_score, policy_name
```

These are created if missing.

---

## Step 2 — Load the three configs

**`config/data.yaml`** tells the pipeline where the workbook lives, what sheets and columns are required, and — most importantly — which policy **names** are ground-truth anomalies for evaluation.

```yaml
dataset_path: data\syntheticdataset\syntheticDataset.xlsx
misconfigured_policies_by_name:
  - tf-secmon-iam-policy         # obvious wildcard
  - AdministratorAccess
  - tf-s3-reader-all-buckets     # subtle: broad resources
  - tf-sts-cross-account-assume  # subtle: cross-account
  ...
```

**`config/neo4j.yaml`** — connection info (Bolt URI, username, password, database) and a `clear_graph: true` toggle that wipes the DB before each run.

**`config/model.yaml`** — Node2Vec hyperparameters (dim=64, walk_length=80, iterations=20), train/test split ratio, which detectors to enable, and grid-search parameter grids.

The label logic is simple but critical:

```
label = -1  if policy_name is in misconfigured_policies_by_name
label = +1  otherwise
```

If none of the listed names exist in the workbook, every sample becomes an anomaly and every metric collapses to zero.

---

## Step 3 — Ingest and normalize the Excel workbook

The pipeline opens the `.xlsx`, reads the four required sheets (`policies`, `users`, `groups`, `roles`), and validates that each has the expected columns (tolerant of case and whitespace).

Then, for every row in the `policies` sheet, it parses the `PolicyObject` column. This column is stored as **Python `repr()` text**, not JSON, so a repair step runs first:

```
Input cell (raw from Excel):
{'Version': '2012-10-17', 'Statement': [{'Effect': 'Allow', 'Action': 's3:*', 'Resource': '*'}]}

After repair: single quotes → double, True/False/None → true/false/null, smart quotes → straight
{"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "s3:*", "Resource": "*"}]}

After json.loads() + flattening:
[{"Effect": "Allow", "Action": "s3:*", "Resource": "*"}]
```

This parsed list is attached to the DataFrame as a hidden column `_policy_statements`. Two more hidden columns (`_policy_parse_ok`, `_policy_parse_error`) track per-row success so the pipeline does **not** crash on a single malformed document — bad rows are logged to `outputs/logs/policy_parse_errors.csv` and the run continues.

**What comes out of Step 3:** a dictionary `tables = {"policies": DataFrame, "users": DataFrame, "groups": DataFrame, "roles": DataFrame}` where the policies frame now has a parsed `_policy_statements` column ready for graph construction.

---

## Step 4 — Build the Neo4j graph

This is the shape of the graph being built, for one example policy `P1`:

```
              (P1:Policy)
             /          \
       ALLOWS            ALLOWS
          /                 \
  (a1:Action                 (a2:Action
   name="s3:GetObject",       name="s3:ListBucket",
   policyKey="P1")            policyKey="P1")
       |                          |
    WORKS_ON                   WORKS_ON
       |                          |
  (r1:Resource               (r2:Resource
   name="arn:...bucket/*",    name="arn:...bucket",
   policyKey="P1")            policyKey="P1")
```

And the principals on top of that:

```
  (alice:User) ─ATTACHED_TO─> (P1:Policy)
  (alice:User) ─PART_OF─────> (devs:Group)
  (devs:Group) ─ATTACHED_TO─> (P1:Policy)
  (admin:Role) ─ATTACHED_TO─> (P1:Policy)
```

The eight node labels used are: `Policy, Action, NotAction, Resource, NotResource, User, Group, Role`.
The six relationship types are: `ALLOWS, DENIES, WORKS_ON, WORKS_NOT_ON, ATTACHED_TO, PART_OF`.

### Key design choice: per-policy **private** Action/Resource nodes

Every Action and Resource carries a `policyKey` property. That means `s3:GetObject` used in policy `P1` and in policy `P2` becomes **two different nodes**, not one shared node. Why?

Because Node2Vec (next step) embeds nodes based on their neighborhoods. If every policy referencing `s3:GetObject` shared the same Action node, all of those policies would be pulled toward the same embedding region regardless of how dangerous each one actually is. Keeping Action/Resource nodes private means each Policy's random walks explore only its own subgraph, so its embedding reflects **its own shape**: how many distinct actions it has, how many resources, allow vs deny ratio, whether it uses `NotAction` bypass, etc.

### Statement effect and special fields

For each parsed statement the pipeline picks `ALLOWS` if `Effect == "Allow"` else `DENIES`, then writes a full cross-product of `(actions × resources)`. Example:

```
Input statement:
{"Effect": "Allow",
 "Action": ["s3:Get*", "s3:List*"],
 "Resource": ["arn:aws:s3:::a", "arn:aws:s3:::b"]}

Creates:
  P → a_get   → r_a
  P → a_get   → r_b
  P → a_list  → r_a
  P → a_list  → r_b
  (4 ALLOWS edges + 4 WORKS_ON edges)
```

`NotAction` entries become `NotAction` labeled nodes; `NotResource` entries become `NotResource` nodes; the action-to-resource edge flips from `WORKS_ON` to `WORKS_NOT_ON`.

### Principals (users, groups, roles)

Each user, group, role becomes a node keyed by its `Id` (or name as fallback). `AttachedPolicies` becomes `ATTACHED_TO` edges into the matching Policy. `Users` on a group becomes `PART_OF` edges from users to the group. The MATCH queries tolerate any reference style (`PolicyName`, `PolicyArn`, `PolicyId`, or policy `key`) so JSON-style and CSV-style exports both work.

### A typical graph size

After loading the synthetic workbook (313 policies, 200 users, 25 groups, 40 roles), `outputs/logs/graph_counts.json` looks roughly like:

```
nodes:       Policy: 313,  Action: ~1500,  Resource: ~1900,  NotAction: ~10,  User: 200,  Group: 25,  Role: 40
relationships: ALLOWS: ~1500,  DENIES: ~30,  WORKS_ON: ~2100,  WORKS_NOT_ON: ~20,  ATTACHED_TO: ~900,  PART_OF: ~180
valid_non_empty_core: true
```

---

## Step 5 — Node2Vec: turn each Policy into a 64-dim vector

Node2Vec is Neo4j GDS's random-walk embedding algorithm. The pipeline does four things:

1. **Drop any stale in-memory projection** from a previous crashed run.
2. **Discover which labels and relationship types actually have data** (GDS errors on empty labels):
   ```cypher
   CALL db.labels() YIELD label RETURN label
   ```
3. **Project the subgraph into memory** (as a named GDS projection, built from the surviving labels/rels).
4. **Run `gds.node2vec.write`** and store the resulting vector on each node:
   ```cypher
   CALL gds.node2vec.write('policy_projection', {
     embeddingDimension: 64, iterations: 20, walkLength: 80,
     writeProperty: 'embeddingNode2vec', randomSeed: 42
   })
   ```

The algorithm performs many random walks starting from each node, treats the walks like sentences, and trains a Word2Vec-style skip-gram model. Result: every node — including every `Policy` — now has a 64-float property:

```
Policy("AdministratorAccess").embeddingNode2vec
  = [ 0.142, -0.381, 0.055, 0.017, ..., 0.220 ]   (64 numbers)
```

Policies with similar _neighborhood shapes_ end up with similar vectors. A policy with "many actions, each with many resources" sits in a different region of the 64-d space than a policy with "one action, one resource."

**Important caveat that shows up in the findings:** Node2Vec sees **topology, not content**. `"ec2:Describe*"` (a dangerous glob) and `"ec2:DescribeInstances"` (a specific call) create identical one-action subgraphs, so they produce identical embeddings. The pipeline cannot distinguish them from the graph alone.

A validation query checks every Policy got a vector of the right length and writes `outputs/logs/embedding_report.json`:

```json
{
  "validation": {
    "total_policy_nodes": 313,
    "policies_with_embedding": 313,
    "coverage_ratio": 1.0,
    "detected_dimension": 64
  }
}
```

---

## Step 6 — Build the ML dataset from the graph

The pipeline reads the vectors back out:

```cypher
MATCH (p:Policy)
RETURN p.key, p.id, p.name, p.embeddingNode2vec AS embedding
```

Then for each row:

- drop if the embedding is missing or the wrong length,
- assign `label = -1` if the policy name is in `misconfigured_policies_by_name`, else `+1`,
- collect into one matrix and one label vector.

Output:

```
X:        np.ndarray of shape (313, 64)    ← one row per policy, 64 features each
y:        np.ndarray of shape (313,)       ← values are 1 (normal) or -1 (anomaly)
metadata: DataFrame with policy_key, policy_id, policy_name, label
```

Typical counts (merged real + synthetic workbook):

```
valid_samples:    ~820
normal_samples:   ~791
anomaly_samples:    29
```

---

## Step 7 — Train/test split (the unsupervised trick)

Anomaly detection models must be **trained on normal data only**. The split function does this:

```
normal indices  = positions where y == +1    (~791 of them)
anomaly indices = positions where y == -1    (  29 of them)

Split the normals 90/10:
  train_idx        = ~709 normals   ← goes into training
  normal_test_idx  =   82 normals   ← held back for testing

Test set = 82 held-out normals + all 29 anomalies = 111 samples, shuffled
```

What the models see:

```
X_train:  shape (~709, 64)   all y = +1
X_test:   shape ( 111, 64)   y contains both +1 and -1
```

The anomalies are completely invisible during training. That's the point — the detector has to learn the shape of "normal" and flag anything else at test time.

A summary lands in `outputs/logs/split_summary.json`.

---

## Step 8 — Optional grid search

If `model.yaml` has `grid_search.enabled: true`, the pipeline expands each model's parameter grid via `itertools.product`, fits every combination on `X_train`, scores it on `X_test`, and picks the combo with the best ROC-AUC for that model.

Example grid for Isolation Forest:

```yaml
isolation_forest:
  n_estimators: [10, 30, 50, 100]
  contamination: [auto]
```

Every trial's metrics are written to `outputs/logs/grid_search_report.json`. The winning params are passed to the real training step. (Caveat: this uses the test set for selection — fine for a research pipeline, not rigorous for a production claim.)

---

## Step 9 — Train the four anomaly detectors

Four unsupervised models are fit on `X_train`, each exposed through scikit-learn's convention:

| Model                    | How it decides "anomaly"                                          | Hyperparameter knob             |
| ------------------------ | ----------------------------------------------------------------- | ------------------------------- |
| **Isolation Forest**     | Random trees isolate points; short average path = anomaly         | `contamination`, `n_estimators` |
| **Local Outlier Factor** | Compare each point's local density to its k neighbors             | `n_neighbors`                   |
| **One-Class SVM**        | Learn a boundary around the normal support (RBF kernel)           | `nu`, `gamma`                   |
| **Elliptic Envelope**    | Fit a robust Gaussian, flag points with high Mahalanobis distance | `contamination`                 |

All of them output `+1` for normal and `-1` for anomaly via `.predict(X_test)`.

Each model also exposes a **continuous score** through `decision_function` or `score_samples`. sklearn's convention is "higher = more normal," so the pipeline flips the sign so that **higher = more anomalous**:

```python
anomaly_score = -model.decision_function(X_test)
```

For each model a CSV is written with one row per test policy:

```
outputs/predictions/isolation_forest_pred.csv:

policy_key, policy_id, policy_name, label, y_true, y_pred, anomaly_score
P001,       ...,       tf-secmon-iam-policy,        -1,  -1,  -1,  0.142
P087,       ...,       tf-s3-reader-all-buckets,    -1,  -1,   1, -0.015   ← missed
P199,       ...,       SomeNormalPolicy,             1,   1,   1, -0.048
...
```

---

## Step 10 — Evaluate and compare

For each model the pipeline computes a confusion matrix (with the anomaly class `-1` as "positive"):

```
y_true = [-1, -1, -1, +1, +1, ...]   (25 anomalies, 29 normals)
y_pred = [-1,  +1, -1, +1, -1, ...]  (whatever the model said)

         predicted +1    predicted -1
true +1       tn              fp
true -1       fn              tp
```

From that: precision, recall, F1 (all for the anomaly class), plus ROC-AUC and PR-AUC using the continuous `anomaly_score`.

Results go to:

- `outputs/metrics/model_metrics.csv` — one row per model, sorted by F1
- `outputs/metrics/comparison.md` — the same as a readable markdown table

---

## Step 11 — Run manifest

Every run writes `outputs/logs/run_manifest.json`:

```json
{
  "timestamp_utc": "...",
  "python_version": "3.11.x",
  "seed": 42,
  "config_paths": { "data": "...", "neo4j": "...", "model": "..." },
  "enabled_stages": [
    "ingest",
    "normalize",
    "graph_build",
    "embed",
    "dataset",
    "split",
    "grid_search",
    "train",
    "evaluate"
  ]
}
```

So any run is reproducible from its configs + the seed.

---

## The `update` subcommand (snapshot diff)

For periodic re-ingests you don't want to rebuild the whole graph. `python -m src.pipeline update --new-data-config config/new_data.yaml`:

1. Parses both old and new workbooks.
2. Sets up `old_keys`, `new_keys` by policy key.
3. Diffs them:
   - `deleted_keys = old − new`
   - `added_keys   = new − old`
   - for keys in both: if `PolicyObject` text differs → `doc_changed`, otherwise if metadata differs → `metadata_changed`.
4. Deletes policies in `deleted_keys` and `doc_changed_keys`, along with **all their private Action/Resource nodes** (safe exactly because those nodes are per-policy).
5. Rebuilds `added_keys` and `doc_changed_keys` from scratch.
6. Patches `metadata_changed_keys` with a light `SET p.name = ..., p.id = ..., p.arn = ...`.
7. Fully refreshes all Users/Groups/Roles (cheaper than diffing attachments).
8. If `recompute_embeddings_after_update: true`, re-runs Node2Vec.

A summary goes to `outputs/logs/update_report.json`.

---

## Typical end-to-end numbers (merged real + synthetic run)

```
Excel rows:           ~820 policies, 200 users, 25 groups, 40 roles
Parse errors:           0
Graph:                ~820 Policy  +  thousands of Action/Resource nodes (private per-policy)
Embeddings:           ~820 policies × 64 floats, coverage 100%
Dataset:              X=(~820,64),  ~791 normals,  29 anomalies
Split:                train=~709 normals,  test=82 normals + 29 anomalies = 111
Models trained:       IsolationForest, LocalOutlierFactor, OneClassSVM, EllipticEnvelope
Outputs:              4 prediction CSVs, 1 metrics CSV, 1 comparison markdown, ~12 log JSONs
```

---

## Known limitations baked into the design

- **Node2Vec ignores node name content.** `"ec2:Describe*"` and `"ec2:DescribeInstances"` look identical as a single Action node. Subtle anomalies that differ only by action-string breadth become indistinguishable from normals.
- **Grid search uses the test set for selection.** Fine for research; not fine for a production claim.
- **Per-policy Action/Resource convention is load-bearing.** If a future change shares Action nodes across policies, the `update` subcommand's subgraph deletion will over-delete.
- **Labels are matched by string.** Renaming a policy silently breaks labeling unless `data.yaml` is updated too.

A natural next step is to run a second feature track alongside the Node2Vec vector — explicit semantic features such as action-breadth flags (`has(*)`, `has(service:*)`), resource-scope flags (cross-account ARN present, `"*"` resource), `NotAction` presence, trust-policy principal width — and concatenate them with the 64-d embedding. The embedding captures shape; those features capture content; together they would cover both the obvious and the subtle threat families.

---

## Findings from the latest run (`outputs/metrics/` — merged dataset)

`model_metrics.csv` after running against the merged workbook (real `dataset.xlsx` + synthetic `syntheticDataset.xlsx`, ~820 policies, 29 anomalies, test = 82 normals + 29 anomalies):

| model                | tn  | fp  | fn  | tp  | precision | recall | f1    | roc_auc | pr_auc |
| -------------------- | --- | --- | --- | --- | --------- | ------ | ----- | ------- | ------ |
| one_class_svm        | 37  | 45  | 15  | 14  | 0.237     | 0.483  | 0.318 | 0.454   | 0.230  |
| isolation_forest     | 77  |  5  | 29  |  0  | 0.000     | 0.000  | 0.000 | 0.484   | 0.245  |
| local_outlier_factor | 80  |  2  | 29  |  0  | 0.000     | 0.000  | 0.000 | 0.434   | 0.226  |
| elliptic_envelope    | 81  |  1  | 29  |  0  | 0.000     | 0.000  | 0.000 | 0.442   | 0.228  |

**This is worse than the synthetic-only run, across the board, and the reason why is the most important finding of the project.**

### What each model is doing

**Isolation Forest — collapsed. F1 = 0.000, ROC-AUC = 0.484 (below random).**
In the synthetic-only run, IF achieved F1=0.62 and ROC-AUC=0.79. Now it flags 0 of the 29 anomalies (fn=29). Its ranking is also dead — ROC-AUC below 0.5 means its anomaly scores slightly *prefer* the normal class. IF has lost the ability to distinguish either structurally.

**Elliptic Envelope — also collapsed. F1 = 0.000, ROC-AUC = 0.442.**
EE predicts 82/82 normals correctly (tn=81, fp=1) and misses every anomaly (fn=29). The robust Gaussian ellipsoid now *contains* every anomaly — the normal cloud got so wide after adding 500 real AWS policies that even the literal-`*` anomalies are inside it.

**Local Outlier Factor — unchanged behavior, still flags nothing.** F1=0, ROC-AUC 0.434. The dense-cluster masking effect documented in the synthetic run persists — LOF's local-density comparison can't see anomalies that are clustered with similar-shaped peers.

**One-Class SVM — only "working" model, but only in a narrow sense.**
F1 = 0.318 (tp=14, fp=45, fn=15). It catches nearly half the anomalies (recall 0.48) but mislabels 45 of 82 normals as anomalies (precision 0.24). ROC-AUC 0.454 — its scoring is essentially random. Any decent F1 here is from over-alerting, not real signal.

### Why adding real AWS policies made things worse

The synthetic dataset was **homogeneous**: normal policies had 1-3 short statements on specific ARNs; anomalies had 12-22 actions on `"*"`. The obvious anomalies stuck out geometrically, so IF/EE could separate them.

The real dataset is ~515 AWS-managed policies, many of which are **legitimately wildcard-heavy by design**:

- `AWSAppMeshReadOnly` uses `Resource: *`
- `AWSCodeDeployRole` deletes autoscaling hooks across all ARNs
- `AWSBackupAdminPolicy` has `backup:*`
- 413 of 515 real policies use `Resource: *` somewhere

To Node2Vec, these AWS-managed policies create the **same topology** as the synthetic "obvious" anomalies: many Action nodes, each connected to wildcard-shaped Resource nodes. Once those 500 real policies enter the training set as "normal," the detectors learn that "many actions on `*`" is the default shape of a normal policy. The obvious anomalies no longer stand out — they look like any other AWS-managed administration policy.

### The central empirical finding

> **Node2Vec on the IAM graph is content-blind. It cannot distinguish a *legitimate* wildcard-heavy AWS-managed policy from a *misconfigured* wildcard-heavy custom policy, because they have the same graph shape.**

As long as the training set was purely synthetic and purely narrow, that limitation was hidden — the synthetic normals never used `"*"`, so wildcard shape = anomaly. The merged run exposes the truth: the models were learning "shape of synthetic normal" not "shape of legitimate AWS policy," and the decision boundary does not transfer.

### Comparison of the two runs

| metric             | synthetic-only | merged (real + synthetic) | change     |
| ------------------ | -------------- | ------------------------- | ---------- |
| train normals      | 259            | ~709                      | +2.7x      |
| anomaly ratio      | 8.0%           | 3.5%                      | down       |
| IF F1              | 0.622          | 0.000                     | collapsed  |
| IF ROC-AUC         | 0.791          | 0.484                     | → random   |
| EE F1              | 0.387          | 0.000                     | collapsed  |
| EE ROC-AUC         | 0.789          | 0.442                     | → random   |
| OCSVM F1           | 0.350          | 0.318                     | stable     |
| LOF F1             | 0.000          | 0.000                     | unchanged  |

The ROC-AUC collapse from 0.79 → 0.44–0.48 is the headline: the rankings are no longer informative. This isn't a threshold-calibration problem anymore; it's a feature-space problem.

### What this means practically

1. **The synthetic-only results were an illusion of effectiveness.** The 0.62 F1 / 0.79 ROC-AUC looked credible but was an artifact of the synthetic normal class being narrower than real IAM usage.
2. **The pipeline cannot be deployed against a realistic IAM corpus as-is.** In production you will always have AWS-managed policies in the dataset, and those will dominate the notion of "normal" in an unsupervised Node2Vec feature space.
3. **This is not a bug in the models — it's a limit of the representation.** All four detectors worked fine on the synthetic data. None of them can recover a signal the embedding does not contain.
4. **The fix is semantic features, and now it is not optional.** A usable detector on a realistic IAM corpus must see *what* a policy does, not just the shape of its subgraph. Features that would have saved this run:
   - `is_aws_managed` flag (derived from ARN: `arn:aws:iam::aws:policy/...`)
   - wildcard density: fraction of actions/resources that are `"*"` or `"svc:*"`
   - cross-account ARN presence (`arn:aws:...::*:role/*`)
   - `NotAction` present
   - `iam:PassRole` present
   - `sts:AssumeRole` present with no `Condition`
   - trust-policy principal width (roles only)

Concatenate a 10-20 dim semantic feature vector with the 64-d Node2Vec embedding and retrain. That gives the detector both the shape (graph) and the content (flags), which is the only way to separate "wildcard-heavy because AWS-managed" from "wildcard-heavy because misconfigured."

### Picking a model on the merged dataset

None of the four is usable out of the box on this data. If forced to pick one until semantic features are added:

| Use case                        | Pick                 | Why                                                                 |
| ------------------------------- | -------------------- | ------------------------------------------------------------------- |
| Any real use                    | **none of the four** | every ROC-AUC is < 0.5 or barely above; no model ranks reliably     |
| Research baseline               | `one_class_svm`      | only non-zero F1 (0.318), but it's just over-alerting               |
| Investigate the collapse itself | `elliptic_envelope`  | its 1.0 precision on synthetic vs 0.0 here is the clearest evidence |

The right move is not to pick among these metrics but to treat them as a go-signal for adding the semantic feature track described above.
