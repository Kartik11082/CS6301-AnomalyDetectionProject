from __future__ import annotations

from pathlib import Path
from typing import Any

from src.core.common import write_json
from src.core.data_ops import load_and_normalize_tables


def validate_dataset(
    data_cfg: dict[str, Any],
    schema_report_path: str | Path,
    parse_error_log_path: str | Path,
    validation_report_path: str | Path,
) -> dict[str, Any]:
    """Validate workbook availability, schema, policy parsing, and label coverage."""
    dataset_path = Path(data_cfg["dataset_path"])
    tables, parse_errors = load_and_normalize_tables(
        data_config=data_cfg,
        schema_report_path=schema_report_path,
        parse_error_log_path=parse_error_log_path,
    )

    policies = tables["policies"]
    policy_names = set(policies["PolicyName"].astype(str))
    policy_ids = set(policies["PolicyId"].astype(str))
    configured_names = [str(name) for name in data_cfg.get("misconfigured_policies_by_name", [])]
    configured_ids = [str(policy_id) for policy_id in data_cfg.get("misconfigured_policies_by_id", [])]
    matched_names = sorted(name for name in configured_names if name in policy_names)
    matched_ids = sorted(policy_id for policy_id in configured_ids if policy_id in policy_ids)
    unmatched_names = sorted(name for name in configured_names if name not in policy_names)
    unmatched_ids = sorted(policy_id for policy_id in configured_ids if policy_id not in policy_ids)

    parse_ok_count = int(policies["_policy_parse_ok"].sum())
    parse_error_count = int((~policies["_policy_parse_ok"]).sum())
    matched_label_count = len(matched_names) + len(matched_ids)
    configured_label_count = len(configured_names) + len(configured_ids)

    report = {
        "ok": bool(parse_error_count == 0 and matched_label_count > 0),
        "dataset_path": str(dataset_path),
        "dataset_exists": dataset_path.exists(),
        "sheets": {
            sheet: {
                "rows": int(frame.shape[0]),
                "columns": [str(column) for column in frame.columns],
            }
            for sheet, frame in tables.items()
        },
        "policy_parse": {
            "total_policies": int(len(policies)),
            "parse_ok": parse_ok_count,
            "parse_errors": parse_error_count,
            "parse_error_log": str(parse_error_log_path),
        },
        "labels": {
            "configured_label_count": configured_label_count,
            "matched_label_count": matched_label_count,
            "matched_names": matched_names,
            "matched_ids": matched_ids,
            "unmatched_names": unmatched_names,
            "unmatched_ids": unmatched_ids,
        },
    }
    if parse_errors.empty:
        report["policy_parse"]["sample_errors"] = []
    else:
        report["policy_parse"]["sample_errors"] = parse_errors.head(5).to_dict(orient="records")

    write_json(validation_report_path, report)
    return report
