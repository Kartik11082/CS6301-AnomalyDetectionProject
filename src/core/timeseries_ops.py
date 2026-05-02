from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from src.core.common import write_json


REQUIRED_SHEETS = ["policies", "users", "groups", "roles"]


def _policy_key(row: pd.Series) -> str:
    policy_id = str(row.get("PolicyId", "")).strip()
    if policy_id:
        return policy_id
    return str(row.get("PolicyName", "")).strip()


def _load_workbook(path: str | Path) -> dict[str, pd.DataFrame]:
    workbook_path = Path(path)
    return {
        sheet: pd.read_excel(workbook_path, sheet_name=sheet, engine="openpyxl").fillna("")
        for sheet in REQUIRED_SHEETS
    }


def _write_workbook(path: str | Path, tables: dict[str, pd.DataFrame]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        for sheet in REQUIRED_SHEETS:
            tables[sheet].to_excel(writer, sheet_name=sheet, index=False)


def _write_yaml(path: str | Path, payload: dict[str, Any]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False)


def _normal_policy_indices(policies: pd.DataFrame, label_names: set[str]) -> list[int]:
    names = policies["PolicyName"].astype(str)
    return [
        int(idx)
        for idx, name in zip(policies.index, names)
        if name not in label_names and not name.startswith("online-added-policy-step-")
    ]


def _make_added_policy(step: int, columns: list[str], row_index: int) -> dict[str, Any]:
    name = f"online-added-policy-step-{step:02d}"
    policy_id = f"ONLINEPOLICY{step:02d}"
    statement = [
        {
            "Effect": "Allow",
            "Action": ["s3:GetObject", "s3:ListBucket"],
            "Resource": [f"arn:aws:s3:::online-demo-bucket-{step:02d}/*"],
        }
    ]
    base = {
        "Unnamed: 0": row_index,
        "PolicyName": name,
        "PolicyId": policy_id,
        "Arn": f"arn:aws:iam::123456789012:policy/{name}",
        "Path": "/online-demo/",
        "DefaultVersionId": "v1",
        "AttachmentCount": 1,
        "CreateDate": f"2026-05-{step:02d}T00:00:00.000Z",
        "UpdateDate": f"2026-05-{step:02d}T00:00:00.000Z",
        "PolicyObject": repr(statement),
    }
    return {column: base.get(column, "") for column in columns}


def _apply_step(
    tables: dict[str, pd.DataFrame],
    step: int,
    label_names: set[str],
) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    updated = {name: frame.copy() for name, frame in tables.items()}
    policies = updated["policies"].copy()
    policy_columns = [str(column) for column in policies.columns]
    normal_indices = _normal_policy_indices(policies, label_names)
    if len(normal_indices) < 3:
        raise RuntimeError("Need at least three normal policies to simulate add/modify/delete/update steps.")

    delete_idx = normal_indices[(step - 1) % len(normal_indices)]
    modify_idx = normal_indices[step % len(normal_indices)]
    metadata_idx = normal_indices[(step + 1) % len(normal_indices)]

    deleted_row = policies.loc[delete_idx].copy()
    modified_row = policies.loc[modify_idx].copy()
    metadata_row = policies.loc[metadata_idx].copy()

    modified_statement = [
        {
            "Effect": "Allow",
            "Action": ["logs:CreateLogGroup", "logs:PutLogEvents", f"logs:DescribeLogStreams"],
            "Resource": [f"arn:aws:logs:us-east-1:123456789012:log-group:/online/step-{step:02d}:*"],
        }
    ]
    policies.at[modify_idx, "PolicyObject"] = repr(modified_statement)
    if "UpdateDate" in policies.columns:
        policies.at[modify_idx, "UpdateDate"] = f"2026-05-{step:02d}T12:00:00.000Z"

    if "AttachmentCount" in policies.columns:
        current = str(policies.at[metadata_idx, "AttachmentCount"]).strip()
        try:
            policies.at[metadata_idx, "AttachmentCount"] = int(float(current)) + 1
        except ValueError:
            policies.at[metadata_idx, "AttachmentCount"] = 1
    elif "Path" in policies.columns:
        policies.at[metadata_idx, "Path"] = "/online-demo-metadata/"

    policies = policies.drop(index=delete_idx).reset_index(drop=True)
    added_row = _make_added_policy(step, policy_columns, row_index=len(policies))
    policies = pd.concat([policies, pd.DataFrame([added_row])], ignore_index=True)
    updated["policies"] = policies

    step_report = {
        "step": step,
        "added_policy": added_row["PolicyName"],
        "deleted_policy": str(deleted_row.get("PolicyName", "")),
        "modified_policy": str(modified_row.get("PolicyName", "")),
        "metadata_updated_policy": str(metadata_row.get("PolicyName", "")),
        "added_count": 1,
        "deleted_count": 1,
        "document_changed_count": 1,
        "metadata_changed_count": 1,
    }
    return updated, step_report


def _build_snapshot_config(
    base_data_cfg: dict[str, Any],
    snapshot_path: Path,
) -> dict[str, Any]:
    cfg = dict(base_data_cfg)
    cfg["dataset_path"] = snapshot_path.as_posix()
    return cfg


def _write_markdown_report(path: str | Path, report: dict[str, Any]) -> None:
    lines = [
        "# Time-Series Online Update Simulation",
        "",
        f"- Base dataset: `{report['base_dataset_path']}`",
        f"- Snapshot directory: `{report['snapshot_dir']}`",
        f"- Steps: `{report['steps']}`",
        "",
        "| step | added | deleted | document changed | metadata changed |",
        "| --- | --- | --- | --- | --- |",
    ]
    for step in report["step_reports"]:
        lines.append(
            "| {step} | {added_policy} | {deleted_policy} | {modified_policy} | {metadata_updated_policy} |".format(
                **step
            )
        )
    lines.extend(
        [
            "",
            "## Replay With Neo4j",
            "",
            "Each generated config can be used with the existing update command, for example:",
            "",
            "```bash",
            "python -m src.pipeline update --old-data-config config/timeseries/snapshot_00.yaml --new-data-config config/timeseries/snapshot_01.yaml",
            "```",
        ]
    )
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def simulate_time_series_updates(
    data_cfg: dict[str, Any],
    steps: int,
    snapshot_dir: str | Path,
    config_dir: str | Path,
    report_json_path: str | Path,
    report_md_path: str | Path,
) -> dict[str, Any]:
    """Generate deterministic old/new workbook snapshots for online-update demos."""
    if steps < 1:
        raise ValueError("steps must be at least 1.")

    base_dataset_path = Path(data_cfg["dataset_path"])
    snapshot_root = Path(snapshot_dir)
    config_root = Path(config_dir)
    tables = _load_workbook(base_dataset_path)
    label_names = {str(name) for name in data_cfg.get("misconfigured_policies_by_name", [])}

    snapshot_paths: list[str] = []
    config_paths: list[str] = []

    snapshot_0 = snapshot_root / "snapshot_00.xlsx"
    _write_workbook(snapshot_0, tables)
    snapshot_paths.append(snapshot_0.as_posix())
    config_0 = config_root / "snapshot_00.yaml"
    _write_yaml(config_0, _build_snapshot_config(data_cfg, snapshot_0))
    config_paths.append(config_0.as_posix())

    current_tables = tables
    step_reports: list[dict[str, Any]] = []
    for step in range(1, steps + 1):
        current_tables, step_report = _apply_step(current_tables, step, label_names)
        snapshot_path = snapshot_root / f"snapshot_{step:02d}.xlsx"
        config_path = config_root / f"snapshot_{step:02d}.yaml"
        _write_workbook(snapshot_path, current_tables)
        _write_yaml(config_path, _build_snapshot_config(data_cfg, snapshot_path))
        snapshot_paths.append(snapshot_path.as_posix())
        config_paths.append(config_path.as_posix())
        step_reports.append(step_report)

    update_pairs = [
        {
            "old_config": config_paths[i],
            "new_config": config_paths[i + 1],
            "command": (
                "python -m src.pipeline update "
                f"--old-data-config {config_paths[i]} "
                f"--new-data-config {config_paths[i + 1]}"
            ),
        }
        for i in range(len(config_paths) - 1)
    ]
    report = {
        "base_dataset_path": base_dataset_path.as_posix(),
        "snapshot_dir": snapshot_root.as_posix(),
        "config_dir": config_root.as_posix(),
        "steps": steps,
        "snapshot_paths": snapshot_paths,
        "config_paths": config_paths,
        "update_pairs": update_pairs,
        "step_reports": step_reports,
    }
    write_json(report_json_path, report)
    _write_markdown_report(report_md_path, report)
    return report
