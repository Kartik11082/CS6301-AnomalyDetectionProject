from __future__ import annotations

import argparse
import warnings
from typing import Any

warnings.filterwarnings("ignore", category=RuntimeWarning, module="sklearn.covariance")

from src.core.common import build_run_manifest, ensure_dirs, load_yaml, write_json
from src.core.data_ops import load_and_normalize_tables
from src.core.graph_ops import build_graph, run_node2vec, update_graph_from_snapshots
from src.core.ml_ops import (
    build_dataset_from_graph,
    create_unsupervised_split,
    evaluate_models,
    grid_search_models,
    train_models,
)


def _prepare_output_dirs() -> None:
    ensure_dirs(
        [
            "outputs/logs",
            "outputs/metrics",
            "outputs/predictions",
        ]
    )


def _load_configs(
    data_config_path: str,
    neo4j_config_path: str,
    model_config_path: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    data_cfg = load_yaml(data_config_path)
    neo4j_cfg = load_yaml(neo4j_config_path)
    model_cfg = load_yaml(model_config_path)
    return data_cfg, neo4j_cfg, model_cfg


def run_full_pipeline(args: argparse.Namespace) -> None:
    """Run ingest -> normalize -> graph -> embed -> dataset -> split -> train -> evaluate."""
    _prepare_output_dirs()
    data_cfg, neo4j_cfg, model_cfg = _load_configs(
        args.data_config, args.neo4j_config, args.model_config
    )

    # Stage 1-3: data ingest + normalization in one call.
    tables, _ = load_and_normalize_tables(
        data_cfg,
        schema_report_path="outputs/logs/schema_report.json",
        parse_error_log_path="outputs/logs/policy_parse_errors.csv",
    )

    build_graph(
        tables=tables,
        neo4j_cfg=neo4j_cfg,
        graph_counts_path="outputs/logs/graph_counts.json",
    )

    if not args.skip_embed:
        run_node2vec(
            neo4j_cfg=neo4j_cfg,
            embedding_cfg=model_cfg.get("embedding", {}),
            report_path="outputs/logs/embedding_report.json",
        )

    dataset = build_dataset_from_graph(
        neo4j_cfg=neo4j_cfg,
        data_cfg=data_cfg,
        embedding_cfg=model_cfg.get("embedding", {}),
        summary_path="outputs/logs/dataset_summary.json",
        metadata_path="outputs/logs/dataset_metadata.csv",
    )

    split = create_unsupervised_split(
        X=dataset["X"],
        y=dataset["y"],
        split_cfg=model_cfg.get("split", {}),
        summary_path="outputs/logs/split_summary.json",
    )

    if not args.skip_train:
        metadata_test = (
            dataset["metadata"].iloc[split["test_idx"]].reset_index(drop=True)
        )

        # Run grid search when enabled to find optimal hyperparameters.
        param_overrides: dict[str, dict[str, Any]] | None = None
        gs_cfg = model_cfg.get("grid_search", {})
        if bool(gs_cfg.get("enabled", False)):
            gs_results = grid_search_models(
                X_train=split["X_train"],
                X_test=split["X_test"],
                y_test=split["y_test"],
                model_cfg=model_cfg,
                report_path="outputs/logs/grid_search_report.json",
            )
            param_overrides = {
                name: result["best_params"]
                for name, result in gs_results.items()
                if result.get("best_params")
            }

        predictions = train_models(
            X_train=split["X_train"],
            X_test=split["X_test"],
            y_test=split["y_test"],
            metadata_test=metadata_test,
            model_cfg=model_cfg,
            prediction_dir="outputs/predictions",
            param_overrides=param_overrides,
        )
        evaluate_models(
            y_test=split["y_test"],
            model_outputs=predictions,
            metrics_csv_path="outputs/metrics/model_metrics.csv",
            comparison_md_path="outputs/metrics/comparison.md",
        )

    manifest = build_run_manifest(
        seed=int(model_cfg.get("seed", 42)),
        data_config_path=args.data_config,
        neo4j_config_path=args.neo4j_config,
        model_config_path=args.model_config,
        enabled_stages=[
            "ingest",
            "normalize",
            "graph_build",
            *([] if args.skip_embed else ["embed"]),
            "dataset",
            "split",
            *(
                []
                if args.skip_train
                else (
                    (
                        ["grid_search"]
                        if bool(model_cfg.get("grid_search", {}).get("enabled", False))
                        else []
                    )
                    + ["train", "evaluate"]
                )
            ),
        ],
    )
    write_json("outputs/logs/run_manifest.json", manifest)


def run_update_pipeline(args: argparse.Namespace) -> None:
    """Run old/new snapshot diff update for graph and embeddings."""
    _prepare_output_dirs()
    old_data_cfg, neo4j_cfg, model_cfg = _load_configs(
        args.old_data_config, args.neo4j_config, args.model_config
    )
    new_data_cfg = load_yaml(args.new_data_config)

    update_graph_from_snapshots(
        old_data_cfg=old_data_cfg,
        new_data_cfg=new_data_cfg,
        neo4j_cfg=neo4j_cfg,
        model_cfg=model_cfg,
        update_report_path="outputs/logs/update_report.json",
        graph_counts_path="outputs/logs/graph_counts_after_update.json",
        embedding_report_path="outputs/logs/embedding_report_after_update.json",
    )


def build_parser() -> argparse.ArgumentParser:
    """Create command line interface parser."""
    parser = argparse.ArgumentParser(
        description="IAM misconfiguration graph + anomaly detection pipeline."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run full pipeline.")
    run_parser.add_argument("--data-config", default="config/data.yaml", type=str)
    run_parser.add_argument("--neo4j-config", default="config/neo4j.yaml", type=str)
    run_parser.add_argument("--model-config", default="config/model.yaml", type=str)
    run_parser.add_argument(
        "--skip-embed", action="store_true", help="Skip Node2Vec embedding stage."
    )
    run_parser.add_argument(
        "--skip-train",
        action="store_true",
        help="Skip model training/evaluation stages.",
    )
    run_parser.set_defaults(func=run_full_pipeline)

    update_parser = subparsers.add_parser(
        "update", help="Update graph using old/new snapshots."
    )
    update_parser.add_argument(
        "--old-data-config", default="config/data.yaml", type=str
    )
    update_parser.add_argument("--new-data-config", required=True, type=str)
    update_parser.add_argument("--neo4j-config", default="config/neo4j.yaml", type=str)
    update_parser.add_argument("--model-config", default="config/model.yaml", type=str)
    update_parser.set_defaults(func=run_update_pipeline)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
