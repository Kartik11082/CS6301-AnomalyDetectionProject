from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any

import pandas as pd

from src.core.common import load_yaml, write_json
from src.core.data_ops import normalize_json_like_text


def _canonical_name(value: str) -> str:
    """Normalize sheet/column names for tolerant comparisons."""
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _is_missing(value: Any) -> bool:
    """Treat empty strings like missing values in addition to NaN."""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, dict, tuple, set)):
        return False
    return bool(pd.isna(value))


def _clean_value(value: Any) -> Any:
    """Convert values into JSON-safe primitives for reporting."""
    if _is_missing(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            value = value.item()
        except Exception:
            pass
    if isinstance(value, str):
        return value.strip()
    return value


def _series_without_missing(series: pd.Series) -> pd.Series:
    """Return non-empty values only."""
    return series.loc[~series.map(_is_missing)]


def _safe_ratio(numerator: int | float, denominator: int | float) -> float:
    """Avoid ZeroDivisionError and return stable rounded ratios."""
    if not denominator:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def _safe_stat(value: float | int | None) -> float | int | None:
    """Convert NaN-like numeric values into JSON-safe values."""
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return round(float(value), 4) if isinstance(value, float) else int(value)


def _top_values(series: pd.Series, limit: int = 5) -> list[dict[str, Any]]:
    """Capture dominant values for low-level distribution checks."""
    cleaned = _series_without_missing(series).map(
        lambda value: str(_clean_value(value))
    )
    if cleaned.empty:
        return []
    counts = cleaned.value_counts(dropna=False).head(limit)
    total = int(cleaned.shape[0])
    return [
        {
            "value": str(index),
            "count": int(count),
            "share": _safe_ratio(int(count), total),
        }
        for index, count in counts.items()
    ]


def _numeric_stats(series: pd.Series) -> dict[str, Any] | None:
    """Summarize numeric columns when most values can be coerced."""
    raw = _series_without_missing(series)
    if raw.empty:
        return None
    numeric = pd.to_numeric(raw, errors="coerce")
    valid = numeric.dropna()
    if valid.empty:
        return None
    if _safe_ratio(valid.shape[0], raw.shape[0]) < 0.8:
        return None
    return {
        "count": int(valid.shape[0]),
        "min": _safe_stat(valid.min()),
        "max": _safe_stat(valid.max()),
        "mean": _safe_stat(valid.mean()),
        "median": _safe_stat(valid.median()),
        "std": _safe_stat(valid.std()),
    }


def _datetime_stats(series: pd.Series) -> dict[str, Any] | None:
    """Summarize datetime columns when most values can be parsed."""
    raw = _series_without_missing(series)
    if raw.empty:
        return None
    if not raw.map(lambda value: isinstance(value, pd.Timestamp)).all():
        date_like_share = raw.astype(str).str.match(r"^\d{4}-\d{2}-\d{2}").mean()
        if float(date_like_share) < 0.8:
            return None
    parsed = pd.to_datetime(raw, errors="coerce", utc=True)
    valid = parsed.dropna()
    if valid.empty:
        return None
    if _safe_ratio(valid.shape[0], raw.shape[0]) < 0.8:
        return None
    return {
        "count": int(valid.shape[0]),
        "min": valid.min().isoformat(),
        "max": valid.max().isoformat(),
    }


def _parse_json_like(value: str) -> Any | None:
    """Best-effort parser for JSON-like export fields."""
    try:
        return json.loads(normalize_json_like_text(value))
    except Exception:
        return None


def _tokenize_cell(value: Any) -> list[str]:
    """Extract list-like members from strings, JSON-like text, or scalars."""
    cleaned = _clean_value(value)
    if cleaned is None:
        return []
    if isinstance(cleaned, list):
        return [str(item).strip() for item in cleaned if str(item).strip()]
    if isinstance(cleaned, dict):
        return [str(key).strip() for key in cleaned.keys() if str(key).strip()]
    if not isinstance(cleaned, str):
        return [str(cleaned).strip()]

    text = cleaned.strip()
    if not text:
        return []

    if text.startswith("[") or text.startswith("{"):
        parsed = _parse_json_like(text)
        if isinstance(parsed, list):
            tokens: list[str] = []
            for item in parsed:
                if isinstance(item, dict):
                    if "PolicyArn" in item:
                        tokens.append(str(item["PolicyArn"]).strip())
                    elif "UserId" in item:
                        tokens.append(str(item["UserId"]).strip())
                    elif "UserName" in item:
                        tokens.append(str(item["UserName"]).strip())
                    else:
                        tokens.append(json.dumps(item, sort_keys=True))
                else:
                    tokens.append(str(item).strip())
            return [token for token in tokens if token]
        if isinstance(parsed, dict):
            return [json.dumps(parsed, sort_keys=True)]

    if "," in text:
        return [part.strip() for part in text.split(",") if part.strip()]

    return [text]


def _token_count_stats(series: pd.Series) -> dict[str, Any] | None:
    """Summarize list cardinality for comma-separated or JSON-like columns."""
    raw = _series_without_missing(series)
    if raw.empty:
        return None

    token_counts = raw.map(lambda value: len(_tokenize_cell(value)))
    if token_counts.empty:
        return None

    list_like_share = _safe_ratio(
        int((token_counts > 1).sum()), int(token_counts.shape[0])
    )
    looks_structured = (
        raw.astype(str).str.contains(",", regex=False).mean() >= 0.3
        or raw.astype(str).str.startswith("[").mean() >= 0.3
    )
    if not looks_structured and list_like_share < 0.2:
        return None

    return {
        "count": int(token_counts.shape[0]),
        "min": _safe_stat(token_counts.min()),
        "max": _safe_stat(token_counts.max()),
        "mean": _safe_stat(token_counts.mean()),
        "median": _safe_stat(token_counts.median()),
        "share_multi_value": list_like_share,
    }


def _infer_column_kind(column_name: str, series: pd.Series) -> str:
    """Use simple heuristics to label ML-relevant column types."""
    canonical = _canonical_name(column_name)
    non_missing = _series_without_missing(series)
    if non_missing.empty:
        return "empty"
    if canonical.startswith("unnamed"):
        return "index_like"

    if _numeric_stats(series) is not None:
        return "numeric"
    if _datetime_stats(series) is not None:
        return "datetime"
    if _token_count_stats(series) is not None:
        return "list_like"

    lowered = non_missing.astype(str).str.strip().str.lower()
    if set(lowered.unique()).issubset({"true", "false", "0", "1", "yes", "no"}):
        return "boolean_like"

    unique_ratio = _safe_ratio(non_missing.nunique(dropna=True), non_missing.shape[0])
    if canonical.endswith(("id", "arn")) or "name" in canonical or unique_ratio >= 0.95:
        return "identifier_like"
    if non_missing.nunique(dropna=True) <= 20 or unique_ratio <= 0.3:
        return "categorical_text"
    return "free_text"


def _string_length_stats(series: pd.Series) -> dict[str, Any] | None:
    """Summarize text length where relevant."""
    raw = _series_without_missing(series)
    if raw.empty:
        return None
    lengths = raw.map(lambda value: len(str(_clean_value(value))))
    return {
        "min": _safe_stat(lengths.min()),
        "max": _safe_stat(lengths.max()),
        "mean": _safe_stat(lengths.mean()),
        "median": _safe_stat(lengths.median()),
    }


def _analyze_column(column_name: str, series: pd.Series) -> dict[str, Any]:
    """Produce a compact per-column profile for schema and ML checks."""
    row_count = int(series.shape[0])
    non_missing = _series_without_missing(series)
    unique_count = int(non_missing.nunique(dropna=True))
    top_values = _top_values(series)

    profile: dict[str, Any] = {
        "name": str(column_name),
        "kind": _infer_column_kind(column_name, series),
        "row_count": row_count,
        "non_null_count": int(non_missing.shape[0]),
        "null_count": row_count - int(non_missing.shape[0]),
        "null_ratio": _safe_ratio(row_count - int(non_missing.shape[0]), row_count),
        "unique_count": unique_count,
        "unique_ratio": _safe_ratio(unique_count, int(non_missing.shape[0])),
        "top_values": top_values,
        "examples": [
            _clean_value(value)
            for value in non_missing.head(3).tolist()
            if _clean_value(value) is not None
        ],
    }

    numeric = _numeric_stats(series)
    if numeric is not None:
        profile["numeric_stats"] = numeric

    datetimes = _datetime_stats(series)
    if datetimes is not None:
        profile["datetime_stats"] = datetimes

    token_counts = _token_count_stats(series)
    if token_counts is not None:
        profile["token_count_stats"] = token_counts

    if profile["kind"] in {
        "categorical_text",
        "free_text",
        "identifier_like",
        "list_like",
    }:
        profile["string_length_stats"] = _string_length_stats(series)

    return profile


def _sheet_ml_signals(column_profiles: list[dict[str, Any]]) -> dict[str, Any]:
    """Highlight likely feature engineering concerns for raw ML work."""
    identifier_like = [
        profile["name"]
        for profile in column_profiles
        if profile["kind"] == "identifier_like"
    ]
    mostly_missing = [
        profile["name"] for profile in column_profiles if profile["null_ratio"] >= 0.5
    ]
    near_constant = [
        profile["name"]
        for profile in column_profiles
        if profile["unique_count"] <= 1
        or (profile["top_values"] and float(profile["top_values"][0]["share"]) >= 0.95)
    ]
    structured = [
        profile["name"]
        for profile in column_profiles
        if profile["kind"] in {"numeric", "datetime", "list_like", "categorical_text"}
    ]
    return {
        "identifier_like_columns": identifier_like,
        "mostly_missing_columns": mostly_missing,
        "near_constant_columns": near_constant,
        "raw_feature_candidate_columns": structured,
    }


def _analyze_sheet(frame: pd.DataFrame, required_columns: list[str]) -> dict[str, Any]:
    """Profile a single worksheet."""
    normalized = frame.copy()
    normalized.columns = [str(column).strip() for column in normalized.columns]
    column_profiles = [
        _analyze_column(column_name, normalized[column_name])
        for column_name in normalized.columns
    ]

    available = {_canonical_name(column): column for column in normalized.columns}
    missing_required = [
        required
        for required in required_columns
        if _canonical_name(required) not in available
    ]

    return {
        "row_count": int(normalized.shape[0]),
        "column_count": int(normalized.shape[1]),
        "columns": [str(column) for column in normalized.columns],
        "missing_required_columns": missing_required,
        "column_profiles": column_profiles,
        "ml_signals": _sheet_ml_signals(column_profiles),
    }


def _load_workbook_analysis(
    workbook_path: str | Path,
    required_columns_map: dict[str, list[str]],
) -> dict[str, Any]:
    """Load and analyze every sheet in a workbook."""
    path = Path(workbook_path)
    workbook = pd.ExcelFile(path, engine="openpyxl")
    sheet_map = {str(sheet).lower(): sheet for sheet in workbook.sheet_names}

    sheets: dict[str, Any] = {}
    for canonical_sheet, actual_sheet in sheet_map.items():
        frame = pd.read_excel(path, sheet_name=actual_sheet, engine="openpyxl")
        sheets[canonical_sheet] = {
            "actual_sheet_name": actual_sheet,
            **_analyze_sheet(
                frame=frame,
                required_columns=required_columns_map.get(canonical_sheet, []),
            ),
        }

    return {
        "path": str(path),
        "sheet_names": workbook.sheet_names,
        "sheet_row_counts": {
            canonical_sheet: int(details["row_count"])
            for canonical_sheet, details in sheets.items()
        },
        "sheets": sheets,
    }


def _compare_workbooks(
    left: dict[str, Any],
    right: dict[str, Any],
    required_columns_map: dict[str, list[str]],
) -> dict[str, Any]:
    """Compare sheet presence and column compatibility between workbooks."""
    left_sheets = set(left["sheets"].keys())
    right_sheets = set(right["sheets"].keys())
    shared_sheets = sorted(left_sheets & right_sheets)

    sheet_comparisons: dict[str, Any] = {}
    exact_schema_match = True
    required_schema_match = True

    for sheet in shared_sheets:
        left_columns = left["sheets"][sheet]["columns"]
        right_columns = right["sheets"][sheet]["columns"]

        left_map = {_canonical_name(column): column for column in left_columns}
        right_map = {_canonical_name(column): column for column in right_columns}
        shared_columns = sorted(set(left_map) & set(right_map))

        missing_required_left = left["sheets"][sheet]["missing_required_columns"]
        missing_required_right = right["sheets"][sheet]["missing_required_columns"]

        same_columns_exact = left_columns == right_columns
        exact_schema_match = exact_schema_match and same_columns_exact
        required_schema_match = (
            required_schema_match
            and not missing_required_left
            and not missing_required_right
        )

        sheet_comparisons[sheet] = {
            "same_columns_exact_order": same_columns_exact,
            "shared_columns": [left_map[name] for name in shared_columns],
            "left_only_columns": [
                left_map[name] for name in sorted(set(left_map) - set(right_map))
            ],
            "right_only_columns": [
                right_map[name] for name in sorted(set(right_map) - set(left_map))
            ],
            "missing_required_left": missing_required_left,
            "missing_required_right": missing_required_right,
            "row_count_left": int(left["sheets"][sheet]["row_count"]),
            "row_count_right": int(right["sheets"][sheet]["row_count"]),
            "row_count_ratio_right_to_left": _safe_ratio(
                int(right["sheets"][sheet]["row_count"]),
                int(left["sheets"][sheet]["row_count"]),
            ),
        }

    only_left = sorted(left_sheets - right_sheets)
    only_right = sorted(right_sheets - left_sheets)
    same_sheet_set = not only_left and not only_right

    return {
        "same_sheet_set": same_sheet_set,
        "left_only_sheets": only_left,
        "right_only_sheets": only_right,
        "exact_schema_match": same_sheet_set and exact_schema_match,
        "required_schema_match": same_sheet_set and required_schema_match,
        "sheet_comparisons": sheet_comparisons,
        "required_columns_reference": required_columns_map,
    }


def _format_brief_list(values: list[str]) -> str:
    """Render small lists in markdown-friendly inline form."""
    if not values:
        return "none"
    return ", ".join(values)


def _distribution_note(
    workbook: dict[str, Any], sheet_name: str, label: str
) -> list[str]:
    """Render a few high-signal distribution notes for a sheet."""
    sheet = workbook["sheets"][sheet_name]
    lines = [
        f"- {label} feature candidates: {_format_brief_list(sheet['ml_signals']['raw_feature_candidate_columns'])}",
        f"- {label} identifier-like: {_format_brief_list(sheet['ml_signals']['identifier_like_columns'])}",
    ]

    interesting_columns = {"AttachedPolicies", "Users", "AttachmentCount"}
    for column in sheet["column_profiles"]:
        if column["name"] not in interesting_columns:
            continue
        if "token_count_stats" in column:
            stats = column["token_count_stats"]
            lines.append(
                f"- {label} `{column['name']}` token counts: "
                f"min={stats['min']}, median={stats['median']}, "
                f"mean={stats['mean']}, max={stats['max']}"
            )
        elif "numeric_stats" in column:
            stats = column["numeric_stats"]
            lines.append(
                f"- {label} `{column['name']}` numeric stats: "
                f"min={stats['min']}, median={stats['median']}, "
                f"mean={stats['mean']}, max={stats['max']}"
            )
    return lines


def _build_markdown_summary(
    left_label: str,
    right_label: str,
    report: dict[str, Any],
) -> str:
    """Create a readable summary for quick review."""
    comparison = report["comparison"]
    lines = [
        "# Dataset Comparison Summary",
        "",
        f"- Left workbook: `{left_label}`",
        f"- Right workbook: `{right_label}`",
        f"- Same sheet set: `{comparison['same_sheet_set']}`",
        f"- Exact raw schema match: `{comparison['exact_schema_match']}`",
        f"- Compatible with required pipeline schema: `{comparison['required_schema_match']}`",
        "",
        "## Sheet Comparison",
    ]

    for sheet, details in comparison["sheet_comparisons"].items():
        lines.extend(
            [
                "",
                f"### {sheet}",
                f"- Row counts: left={details['row_count_left']}, right={details['row_count_right']}",
                f"- Exact same columns: `{details['same_columns_exact_order']}`",
                f"- Left-only columns: {_format_brief_list(details['left_only_columns'])}",
                f"- Right-only columns: {_format_brief_list(details['right_only_columns'])}",
            ]
        )
        lines.extend(_distribution_note(report["left"], sheet, "Left"))
        lines.extend(_distribution_note(report["right"], sheet, "Right"))

    lines.extend(
        [
            "",
            "## ML Notes",
            "",
            "- `identifier_like_columns` are usually poor raw tabular features and should be encoded, dropped, or replaced with engineered statistics.",
            "- `list_like` fields such as `AttachedPolicies` and `Users` are more useful through counts, cardinalities, or graph-derived features than raw strings.",
            "- If `required_schema_match` is true but `exact_schema_match` is false, both files fit the current pipeline contract but are not identical raw exports.",
        ]
    )
    return "\n".join(lines) + "\n"


def _print_console_summary(
    left_path: str,
    right_path: str,
    comparison: dict[str, Any],
) -> None:
    """Emit a short CLI summary."""
    print(f"Left workbook:  {left_path}")
    print(f"Right workbook: {right_path}")
    print(f"Same sheet set: {comparison['same_sheet_set']}")
    print(f"Exact raw schema match: {comparison['exact_schema_match']}")
    print(f"Required pipeline schema match: {comparison['required_schema_match']}")
    print("")
    for sheet, details in comparison["sheet_comparisons"].items():
        print(
            f"[{sheet}] left_rows={details['row_count_left']} "
            f"right_rows={details['row_count_right']} "
            f"exact_cols={details['same_columns_exact_order']}"
        )
        if details["left_only_columns"]:
            print(f"  left_only: {', '.join(details['left_only_columns'])}")
        if details["right_only_columns"]:
            print(f"  right_only: {', '.join(details['right_only_columns'])}")


def build_parser() -> argparse.ArgumentParser:
    """Create CLI arguments for dataset comparison."""
    parser = argparse.ArgumentParser(
        description="Analyze and compare two IAM workbook datasets."
    )
    parser.add_argument("--left", default="data/dataset.xlsx", type=str)
    parser.add_argument(
        "--right",
        default="data/syntheticdataset/syntheticDataset.xlsx",
        type=str,
    )
    parser.add_argument("--data-config", default="config/data.yaml", type=str)
    parser.add_argument(
        "--json-out",
        default="outputs/logs/dataset_comparison_report.json",
        type=str,
    )
    parser.add_argument(
        "--markdown-out",
        default="outputs/logs/dataset_comparison_summary.md",
        type=str,
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    data_cfg = load_yaml(args.data_config)
    required_columns_map = {
        str(sheet).lower(): columns
        for sheet, columns in data_cfg.get("required_columns", {}).items()
    }

    left_analysis = _load_workbook_analysis(args.left, required_columns_map)
    right_analysis = _load_workbook_analysis(args.right, required_columns_map)
    comparison = _compare_workbooks(
        left=left_analysis,
        right=right_analysis,
        required_columns_map=required_columns_map,
    )

    report = {
        "left": left_analysis,
        "right": right_analysis,
        "comparison": comparison,
    }

    write_json(args.json_out, report)
    markdown = _build_markdown_summary(args.left, args.right, report)
    Path(args.markdown_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.markdown_out).write_text(markdown, encoding="utf-8")

    _print_console_summary(args.left, args.right, comparison)
    print("")
    print(f"JSON report written to: {args.json_out}")
    print(f"Markdown summary written to: {args.markdown_out}")


if __name__ == "__main__":
    main()
