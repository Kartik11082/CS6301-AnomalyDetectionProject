from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.covariance import EllipticEnvelope
from sklearn.ensemble import IsolationForest
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split
from sklearn.neighbors import LocalOutlierFactor
from sklearn.svm import OneClassSVM

from src.core.common import write_json
from src.core.graph_ops import create_verified_driver


def build_dataset_from_graph(
    neo4j_cfg: dict[str, Any],
    data_cfg: dict[str, Any],
    embedding_cfg: dict[str, Any],
    summary_path: str,
    metadata_path: str,
) -> dict[str, Any]:
    """Read Policy embeddings from Neo4j and build X/y/metadata."""
    write_property = embedding_cfg.get("write_property", "embeddingNode2vec")
    expected_dim = int(embedding_cfg.get("embedding_dimension", 128))
    database = neo4j_cfg.get("database", "neo4j")
    driver = create_verified_driver(neo4j_cfg, stage="dataset")

    try:
        with driver.session(database=database) as session:
            result = session.run(
                """
                MATCH (p:Policy)
                RETURN p.key AS policy_key, p.id AS policy_id, p.name AS policy_name, p[$property] AS embedding
                """,
                {"property": write_property},
            )
            rows = [dict(record) for record in result]
    finally:
        driver.close()

    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError("No Policy nodes were found in Neo4j. Build graph first.")

    misconfigured_names = set(data_cfg.get("misconfigured_policies_by_name", []))
    misconfigured_ids = set(data_cfg.get("misconfigured_policies_by_id", []))

    valid_rows: list[dict[str, Any]] = []
    dropped_missing = 0
    dropped_bad_dim = 0
    for _, row in df.iterrows():
        embedding = row.get("embedding")
        if not isinstance(embedding, list) or not embedding:
            dropped_missing += 1
            continue
        if len(embedding) != expected_dim:
            dropped_bad_dim += 1
            continue

        policy_name = str(row.get("policy_name", ""))
        policy_id = str(row.get("policy_id", ""))
        label = -1 if (policy_name in misconfigured_names or policy_id in misconfigured_ids) else 1
        valid_rows.append(
            {
                "policy_key": str(row.get("policy_key", "")),
                "policy_id": policy_id,
                "policy_name": policy_name,
                "label": label,
                "embedding": embedding,
            }
        )

    if not valid_rows:
        raise RuntimeError("All candidate records were dropped due to missing/invalid embeddings.")

    records = pd.DataFrame(valid_rows)
    X = np.array(records["embedding"].tolist(), dtype=float)
    y = records["label"].to_numpy(dtype=int)
    metadata = records[["policy_key", "policy_id", "policy_name"]].copy()
    metadata["label"] = y
    metadata.to_csv(metadata_path, index=False)

    summary = {
        "total_policy_nodes_seen": int(len(df)),
        "valid_samples": int(len(records)),
        "normal_samples": int((y == 1).sum()),
        "anomaly_samples": int((y == -1).sum()),
        "dropped_missing_embedding": int(dropped_missing),
        "dropped_bad_dimension": int(dropped_bad_dim),
        "feature_dimension": int(X.shape[1]),
    }
    write_json(summary_path, summary)
    return {"X": X, "y": y, "metadata": metadata, "summary": summary}


def create_unsupervised_split(X: np.ndarray, y: np.ndarray, split_cfg: dict[str, Any], summary_path: str) -> dict[str, Any]:
    """Split for anomaly detection: train on normal, test on normal+anomaly."""
    seed = int(split_cfg.get("seed", 42))
    test_size = float(split_cfg.get("normal_test_size", 0.2))
    train_normal_only = bool(split_cfg.get("train_normal_only", True))

    normal_idx = np.where(y == 1)[0]
    anomaly_idx = np.where(y == -1)[0]
    if len(normal_idx) < 2:
        raise RuntimeError("Need at least 2 normal samples to create a train/test split.")

    train_idx, normal_test_idx = train_test_split(normal_idx, test_size=test_size, random_state=seed, shuffle=True)
    final_train_idx = train_idx if train_normal_only else np.concatenate([train_idx, anomaly_idx])
    test_idx = np.concatenate([normal_test_idx, anomaly_idx])

    rng = np.random.default_rng(seed)
    rng.shuffle(test_idx)

    split = {
        "train_idx": final_train_idx,
        "test_idx": test_idx,
        "X_train": X[final_train_idx],
        "y_train": y[final_train_idx],
        "X_test": X[test_idx],
        "y_test": y[test_idx],
    }
    summary = {
        "seed": seed,
        "normal_test_size": test_size,
        "train_normal_only": train_normal_only,
        "train_size": int(len(final_train_idx)),
        "test_size": int(len(test_idx)),
        "train_normal_count": int((split["y_train"] == 1).sum()),
        "train_anomaly_count": int((split["y_train"] == -1).sum()),
        "test_normal_count": int((split["y_test"] == 1).sum()),
        "test_anomaly_count": int((split["y_test"] == -1).sum()),
    }
    write_json(summary_path, summary)
    split["summary"] = summary
    return split


def _build_models(model_cfg: dict[str, Any]) -> dict[str, Any]:
    """Build anomaly models from config in one place."""
    seed = int(model_cfg.get("seed", 42))
    enabled = model_cfg.get(
        "enabled_models",
        ["isolation_forest", "local_outlier_factor", "one_class_svm", "elliptic_envelope"],
    )
    params = model_cfg.get("hyperparameters", {})

    models: dict[str, Any] = {}
    if "isolation_forest" in enabled:
        models["isolation_forest"] = IsolationForest(random_state=seed, **params.get("isolation_forest", {}))
    if "local_outlier_factor" in enabled:
        models["local_outlier_factor"] = LocalOutlierFactor(novelty=True, **params.get("local_outlier_factor", {}))
    if "one_class_svm" in enabled:
        models["one_class_svm"] = OneClassSVM(**params.get("one_class_svm", {}))
    if "elliptic_envelope" in enabled:
        models["elliptic_envelope"] = EllipticEnvelope(random_state=seed, **params.get("elliptic_envelope", {}))
    return models


def _make_model(name: str, seed: int, params: dict[str, Any]) -> Any:
    """Instantiate a single anomaly detector by name with given params."""
    if name == "isolation_forest":
        return IsolationForest(random_state=seed, **params)
    if name == "local_outlier_factor":
        return LocalOutlierFactor(novelty=True, **params)
    if name == "one_class_svm":
        return OneClassSVM(**params)
    if name == "elliptic_envelope":
        return EllipticEnvelope(random_state=seed, **params)
    raise ValueError(f"Unknown model: {name}")


def _score_model(
    model: Any,
    X_train: np.ndarray,
    X_test: np.ndarray,
    y_test: np.ndarray,
) -> dict[str, float]:
    """Fit a model and return evaluation metrics."""
    model.fit(X_train)
    y_pred = model.predict(X_test)

    y_binary = (y_test == -1).astype(int)
    has_both_classes = len(np.unique(y_test)) > 1

    scores: np.ndarray | None = None
    if hasattr(model, "decision_function"):
        scores = -model.decision_function(X_test)
    elif hasattr(model, "score_samples"):
        scores = -model.score_samples(X_test)

    result: dict[str, float] = {
        "precision": float(precision_score(y_test, y_pred, pos_label=-1, zero_division=0)),
        "recall": float(recall_score(y_test, y_pred, pos_label=-1, zero_division=0)),
        "f1": float(f1_score(y_test, y_pred, pos_label=-1, zero_division=0)),
    }
    if scores is not None and has_both_classes:
        result["roc_auc"] = float(roc_auc_score(y_binary, scores))
        result["pr_auc"] = float(average_precision_score(y_binary, scores))
    else:
        result["roc_auc"] = float("nan")
        result["pr_auc"] = float("nan")
    return result


def _expand_param_grid(grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    """Expand a parameter grid dict into a list of all combinations."""
    keys = sorted(grid.keys())
    values = [grid[k] for k in keys]
    combos: list[dict[str, Any]] = []
    for combo in itertools.product(*values):
        combos.append(dict(zip(keys, combo)))
    return combos


def grid_search_models(
    X_train: np.ndarray,
    X_test: np.ndarray,
    y_test: np.ndarray,
    model_cfg: dict[str, Any],
    report_path: str | Path,
) -> dict[str, dict[str, Any]]:
    """Run grid search over param grids for each enabled model.

    Selects the best parameter combination per model using the metric
    specified in model_cfg['grid_search']['metric'] (default: roc_auc).
    Returns a dict mapping model name -> {'best_params': ..., 'best_score': ..., 'all_results': ...}.
    """
    gs_cfg = model_cfg.get("grid_search", {})
    param_grids = gs_cfg.get("param_grids", {})
    selection_metric = gs_cfg.get("metric", "roc_auc")
    seed = int(model_cfg.get("seed", 42))
    enabled = model_cfg.get(
        "enabled_models",
        ["isolation_forest", "local_outlier_factor", "one_class_svm", "elliptic_envelope"],
    )

    best_per_model: dict[str, dict[str, Any]] = {}

    for name in enabled:
        grid = param_grids.get(name)
        if not grid:
            continue

        combos = _expand_param_grid(grid)
        trial_results: list[dict[str, Any]] = []
        best_score = float("-inf")
        best_params: dict[str, Any] = {}

        for params in combos:
            try:
                model = _make_model(name, seed, params)
                metrics = _score_model(model, X_train, X_test, y_test)
                score = metrics.get(selection_metric, float("nan"))
                trial_results.append({"params": params, "metrics": metrics})

                if not np.isnan(score) and score > best_score:
                    best_score = score
                    best_params = params
            except Exception as exc:
                trial_results.append({"params": params, "error": str(exc)})

        best_per_model[name] = {
            "best_params": best_params,
            "best_score": float(best_score) if not np.isinf(best_score) else None,
            "selection_metric": selection_metric,
            "trials": len(trial_results),
            "all_results": trial_results,
        }

    write_json(report_path, best_per_model)
    return best_per_model


def train_models(
    X_train: np.ndarray,
    X_test: np.ndarray,
    y_test: np.ndarray,
    metadata_test: pd.DataFrame,
    model_cfg: dict[str, Any],
    prediction_dir: str | Path,
    param_overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Fit enabled anomaly models and write per-model prediction files.

    If param_overrides is provided (e.g. from grid_search_models), those
    parameters are used instead of the defaults in model_cfg['hyperparameters'].
    """
    prediction_dir = Path(prediction_dir)
    prediction_dir.mkdir(parents=True, exist_ok=True)

    if param_overrides:
        seed = int(model_cfg.get("seed", 42))
        enabled = model_cfg.get(
            "enabled_models",
            ["isolation_forest", "local_outlier_factor", "one_class_svm", "elliptic_envelope"],
        )
        models: dict[str, Any] = {}
        default_params = model_cfg.get("hyperparameters", {})
        for name in enabled:
            params = param_overrides.get(name, default_params.get(name, {}))
            models[name] = _make_model(name, seed, params)
    else:
        models = _build_models(model_cfg)

    if not models:
        raise RuntimeError("No models enabled in model configuration.")

    results: dict[str, dict[str, Any]] = {}
    for name, model in models.items():
        model.fit(X_train)
        y_pred = model.predict(X_test)

        # Convert to anomaly-oriented score: larger means more anomalous.
        scores: np.ndarray | None = None
        if hasattr(model, "decision_function"):
            scores = -model.decision_function(X_test)
        elif hasattr(model, "score_samples"):
            scores = -model.score_samples(X_test)

        frame = metadata_test.copy()
        frame["y_true"] = y_test
        frame["y_pred"] = y_pred
        if scores is not None:
            frame["anomaly_score"] = scores
        frame.to_csv(prediction_dir / f"{name}_pred.csv", index=False)
        results[name] = {"y_pred": y_pred, "scores": scores}

    return results


def evaluate_models(
    y_test: np.ndarray,
    model_outputs: dict[str, dict[str, Any]],
    metrics_csv_path: str | Path,
    comparison_md_path: str | Path,
) -> pd.DataFrame:
    """Compute metrics for all models and write CSV + markdown comparison."""
    rows: list[dict[str, Any]] = []
    for name, output in model_outputs.items():
        y_pred = output["y_pred"]
        scores = output.get("scores")
        matrix = confusion_matrix(y_test, y_pred, labels=[1, -1])
        tn, fp, fn, tp = matrix.ravel()

        row = {
            "model": name,
            "tn": int(tn),
            "fp": int(fp),
            "fn": int(fn),
            "tp": int(tp),
            "precision": float(precision_score(y_test, y_pred, pos_label=-1, zero_division=0)),
            "recall": float(recall_score(y_test, y_pred, pos_label=-1, zero_division=0)),
            "f1": float(f1_score(y_test, y_pred, pos_label=-1, zero_division=0)),
        }
        if scores is not None and len(np.unique(y_test)) > 1:
            y_binary = (y_test == -1).astype(int)
            row["roc_auc"] = float(roc_auc_score(y_binary, scores))
            row["pr_auc"] = float(average_precision_score(y_binary, scores))
        else:
            row["roc_auc"] = float("nan")
            row["pr_auc"] = float("nan")
        rows.append(row)

    report = pd.DataFrame(rows).sort_values(by="f1", ascending=False).reset_index(drop=True)
    Path(metrics_csv_path).parent.mkdir(parents=True, exist_ok=True)
    report.to_csv(metrics_csv_path, index=False)

    best_model = report.iloc[0]["model"] if not report.empty else "n/a"
    columns = list(report.columns)
    with Path(comparison_md_path).open("w", encoding="utf-8") as handle:
        handle.write("# Model Comparison\n\n")
        handle.write(f"Best model by F1: `{best_model}`\n\n")
        handle.write("| " + " | ".join(columns) + " |\n")
        handle.write("| " + " | ".join(["---"] * len(columns)) + " |\n")
        for _, row in report.iterrows():
            handle.write("| " + " | ".join(str(row[col]) for col in columns) + " |\n")

    return report

