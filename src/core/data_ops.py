from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd

from src.core.common import write_json


def _canonical_name(value: str) -> str:
    """Normalize a column name for robust comparisons."""
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _validate_required_columns(
    frame: pd.DataFrame, required: list[str], sheet_name: str
) -> None:
    """Fail early when required schema columns are missing."""
    available = {_canonical_name(col): str(col) for col in frame.columns}
    missing = [col for col in required if _canonical_name(col) not in available]
    if missing:
        raise ValueError(
            f"Missing required columns in sheet '{sheet_name}': {missing}. "
            f"Available columns: {list(frame.columns)}"
        )


def normalize_json_like_text(raw_text: str) -> str:
    """Repair common JSON-like formatting issues from exported IAM fields."""
    text = str(raw_text).strip()
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("'", '"')
    text = re.sub(r"\bTrue\b", "true", text)
    text = re.sub(r"\bFalse\b", "false", text)
    text = re.sub(r"\bNone\b", "null", text)
    return text


def _as_list(value: Any) -> list[Any]:
    """Convert scalar-or-list values into list form."""
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    return [value]


def parse_policy_statements(policy_text: str) -> list[dict[str, Any]]:
    """Parse a policy document into a list of statement dictionaries."""
    parsed = json.loads(normalize_json_like_text(policy_text))

    if isinstance(parsed, dict):
        statements = parsed.get("Statement", parsed)
    else:
        statements = parsed

    if not isinstance(statements, (dict, list)):
        raise ValueError("Parsed policy is neither dict nor list.")

    normalized: list[dict[str, Any]] = []
    for statement in _as_list(statements):
        if isinstance(statement, dict):
            normalized.append(statement)
    return normalized


def parse_json_like_list(value: Any) -> list[dict[str, Any]]:
    """Parse AttachedPolicies/Users fields that are often JSON-like strings."""
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, str):
        return []

    try:
        parsed = json.loads(normalize_json_like_text(value))
    except Exception:
        return []

    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    return []


def load_excel_tables(
    data_config: dict[str, Any], schema_report_path: str | Path
) -> dict[str, pd.DataFrame]:
    """Load configured workbook sheets and validate required columns."""
    dataset_path = Path(data_config["dataset_path"])
    required_sheets = data_config.get(
        "required_sheets", ["policies", "users", "groups", "roles"]
    )
    required_columns: dict[str, list[str]] = data_config.get("required_columns", {})

    if not dataset_path.exists():
        raise FileNotFoundError(f"Dataset not found: {dataset_path}")

    workbook = pd.ExcelFile(dataset_path, engine="openpyxl")
    available_sheet_map = {
        str(name).lower(): str(name) for name in workbook.sheet_names
    }

    missing_sheets = [
        name for name in required_sheets if str(name).lower() not in available_sheet_map
    ]
    if missing_sheets:
        raise ValueError(
            f"Missing required sheets: {missing_sheets}. Available sheets: {workbook.sheet_names}"
        )

    tables: dict[str, pd.DataFrame] = {}
    schema_report: dict[str, Any] = {"dataset_path": str(dataset_path), "sheets": {}}

    for sheet in required_sheets:
        actual_sheet = available_sheet_map[str(sheet).lower()]
        frame = pd.read_excel(dataset_path, sheet_name=actual_sheet, engine="openpyxl")
        frame.columns = [str(col).strip() for col in frame.columns]
        frame.fillna("", inplace=True)
        tables[sheet] = frame

        required_for_sheet = required_columns.get(sheet, [])
        if required_for_sheet:
            _validate_required_columns(frame, required_for_sheet, sheet)

        schema_report["sheets"][sheet] = {
            "actual_sheet_name": actual_sheet,
            "rows": int(frame.shape[0]),
            "columns": [str(col) for col in frame.columns],
        }

    write_json(schema_report_path, schema_report)
    return tables


def normalize_tables(
    tables: dict[str, pd.DataFrame],
    parse_error_log_path: str | Path,
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """Clean workbook tables and parse policy documents without crashing."""
    normalized = {name: frame.copy() for name, frame in tables.items()}

    for frame in normalized.values():
        frame.fillna("", inplace=True)

    if "roles" in normalized:
        # Some dumps include dotted role fields; normalize once here.
        normalized["roles"].columns = [
            str(col).replace(".", "").strip() for col in normalized["roles"].columns
        ]

    policies = normalized["policies"].copy()
    if "ExtraPolicySpace" in policies.columns:
        # Original MISDET data sometimes splits PolicyObject across two columns.
        policies["PolicyObject"] = policies["PolicyObject"].astype(str) + policies[
            "ExtraPolicySpace"
        ].astype(str)

    parsed_statements: list[list[dict[str, Any]]] = []
    parse_ok: list[bool] = []
    parse_errors: list[str] = []
    error_rows: list[dict[str, Any]] = []

    for _, row in policies.iterrows():
        policy_name = str(row.get("PolicyName", ""))
        policy_id = str(row.get("PolicyId", ""))
        text = str(row.get("PolicyObject", ""))

        try:
            statements = parse_policy_statements(text)
            parsed_statements.append(statements)
            parse_ok.append(True)
            parse_errors.append("")
        except Exception as exc:
            parsed_statements.append([])
            parse_ok.append(False)
            parse_errors.append(str(exc))
            error_rows.append(
                {
                    "PolicyName": policy_name,
                    "PolicyId": policy_id,
                    "error": str(exc),
                    "policy_text_sample": text[:500],
                }
            )

    policies["_policy_statements"] = parsed_statements
    policies["_policy_parse_ok"] = parse_ok
    policies["_policy_parse_error"] = parse_errors
    normalized["policies"] = policies

    errors_df = pd.DataFrame(error_rows)
    Path(parse_error_log_path).parent.mkdir(parents=True, exist_ok=True)
    errors_df.to_csv(parse_error_log_path, index=False)
    return normalized, errors_df


def load_and_normalize_tables(
    data_config: dict[str, Any],
    schema_report_path: str | Path,
    parse_error_log_path: str | Path,
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """Convenience wrapper to execute ingest + normalize in one call."""
    tables = load_excel_tables(data_config, schema_report_path=schema_report_path)
    return normalize_tables(tables, parse_error_log_path=parse_error_log_path)
