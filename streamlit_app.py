from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import altair as alt
import pandas as pd
import streamlit as st

from src.core.common import load_yaml
from src.core.data_ops import parse_policy_statements


SNAPSHOT_DIR = Path("data/timeseries")
PREDICTION_DIR = Path("outputs/predictions")
METRICS_DIR = Path("outputs/metrics")
UPDATE_REPORT = Path("outputs/logs/timeseries_update_report.json")
DATA_CONFIG = Path("config/data.yaml")

GREEN = "#16a34a"
RED = "#dc2626"
BLUE = "#2563eb"
DARK = "#0f172a"


def list_snapshots() -> list[Path]:
    return sorted(SNAPSHOT_DIR.glob("snapshot_*.xlsx"))


@st.cache_data
def load_snapshot(path: str) -> dict[str, pd.DataFrame]:
    tables = {}
    for sheet in ["policies", "users", "groups", "roles"]:
        tables[sheet] = pd.read_excel(path, sheet_name=sheet, engine="openpyxl").fillna("")
    return tables


@st.cache_data
def load_predictions(model_name: str) -> pd.DataFrame:
    path = PREDICTION_DIR / f"{model_name}_pred.csv"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


@st.cache_data
def load_metrics(dataset_name: str) -> pd.DataFrame:
    path = METRICS_DIR / f"model_metrics_{dataset_name}.csv"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


@st.cache_data
def load_update_report() -> dict[str, Any]:
    if not UPDATE_REPORT.exists():
        return {"step_reports": []}
    return json.loads(UPDATE_REPORT.read_text(encoding="utf-8"))


def get_label_names() -> set[str]:
    if not DATA_CONFIG.exists():
        return set()
    cfg = load_yaml(DATA_CONFIG)
    return {str(name) for name in cfg.get("misconfigured_policies_by_name", [])}


def get_snapshot_number(path: Path) -> int:
    return int(path.stem.split("_")[-1])


def prediction_lookup(predictions: pd.DataFrame) -> dict[str, dict[str, Any]]:
    lookup = {}
    if predictions.empty:
        return lookup
    for _, row in predictions.iterrows():
        lookup[str(row["policy_name"])] = {
            "y_pred": int(row["y_pred"]),
            "anomaly_score": float(row["anomaly_score"]),
        }
    return lookup


def escape_dot(value: Any, limit: int = 34) -> str:
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def values_as_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def policy_color(policy_name: str, lookup: dict[str, dict[str, Any]]) -> str:
    row = lookup.get(policy_name)
    if row is None:
        return GREEN
    if row["y_pred"] == -1:
        return RED
    return GREEN


def policy_status(policy_name: str, lookup: dict[str, dict[str, Any]]) -> str:
    row = lookup.get(policy_name)
    if row is None:
        return "Good"
    if row["y_pred"] == -1:
        return "Risky"
    return "Good"


def update_row(snapshot_number: int, update_report: dict[str, Any]) -> dict[str, Any] | None:
    if snapshot_number == 0:
        return None
    for row in update_report.get("step_reports", []):
        if int(row["step"]) == snapshot_number:
            return row
    return None


def update_text(snapshot_number: int, update_report: dict[str, Any]) -> str:
    row = update_row(snapshot_number, update_report)
    if row is None:
        return "Initial IAM snapshot."
    return (
        f"Added `{row['added_policy']}`, deleted `{row['deleted_policy']}`, "
        f"modified `{row['modified_policy']}`, metadata updated `{row['metadata_updated_policy']}`."
    )


def visible_snapshots(all_snapshots: list[Path]) -> list[Path]:
    if st.session_state.get("snapshot_4_added", False):
        return all_snapshots[:5]
    return all_snapshots[:4]


def current_snapshot(all_snapshots: list[Path]) -> Path:
    shown = visible_snapshots(all_snapshots)
    return shown[-1]


def prediction_rows_for_snapshot(
    policies: pd.DataFrame,
    predictions: pd.DataFrame,
) -> pd.DataFrame:
    if predictions.empty:
        return pd.DataFrame()
    names = set(policies["PolicyName"].astype(str))
    rows = predictions[predictions["policy_name"].astype(str).isin(names)].copy()
    return rows.sort_values(["y_pred", "anomaly_score"], ascending=[True, False])


def changed_policies_for_snapshot(snapshot_number: int, report: dict[str, Any]) -> list[str]:
    row = update_row(snapshot_number, report)
    if row is None:
        return []
    return [
        row["added_policy"],
        row["deleted_policy"],
        row["modified_policy"],
        row["metadata_updated_policy"],
    ]


def graph_policy_names_for_demo(scored_rows: pd.DataFrame, policies: pd.DataFrame) -> list[str]:
    available = set(policies["PolicyName"].astype(str))
    names = []
    policy_text_by_name = {
        str(row["PolicyName"]): str(row.get("PolicyObject", ""))
        for _, row in policies.iterrows()
    }

    risky_rows = scored_rows[scored_rows["y_pred"] == -1]
    safe_rows = scored_rows[scored_rows["y_pred"] != -1].copy()

    if not risky_rows.empty:
        risky_name = str(risky_rows.iloc[0]["policy_name"])
        if risky_name in available:
            names.append(risky_name)

    if not safe_rows.empty:
        safe_rows["graph_edges"] = safe_rows["policy_name"].map(
            lambda name: projected_edge_count(policy_text_by_name.get(str(name), ""))
        )
        safe_rows = safe_rows.sort_values(["graph_edges", "anomaly_score"], ascending=[True, False])

    for _, row in safe_rows.iterrows():
        safe_name = str(row["policy_name"])
        if safe_name in available and safe_name not in names:
            names.append(safe_name)
        if len(names) == 4:
            break

    if len(names) < 4:
        for name in policies["PolicyName"].astype(str):
            if name not in names:
                names.append(name)
            if len(names) == 4:
                break

    return names


def projected_edge_count(policy_text: str) -> int:
    try:
        statements = parse_policy_statements(policy_text)
    except Exception:
        return 10_000

    count = 0
    for statement in statements:
        actions = values_as_list(statement.get("Action"))
        not_actions = values_as_list(statement.get("NotAction"))
        resources = values_as_list(statement.get("Resource"))
        not_resources = values_as_list(statement.get("NotResource"))

        count += len(actions) + len(not_actions)
        count += len(actions) * (len(resources) + len(not_resources))
        count += len(not_actions) * (len(resources) + len(not_resources))

    if count == 0:
        return 10_000
    return count


def graph_header(width: float | None = 8, height: float | None = 4) -> list[str]:
    graph_attrs = 'bgcolor="transparent", pad="0.08", nodesep="0.22", ranksep="0.34", margin="0"'
    if width is not None and height is not None:
        graph_attrs += f', size="{width},{height}!", ratio="compress"'
    return [
        "digraph G {",
        "rankdir=LR;",
        f"graph [{graph_attrs}];",
        'node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=9, margin="0.08,0.05"];',
        'edge [color="#94a3b8", fontcolor="#cbd5e1", fontname="Helvetica", fontsize=8, arrowsize=0.6];',
    ]


def add_policy_statement_edges(
    lines: list[str],
    policy_node_id: str,
    policy_text: str,
    node_prefix: str,
) -> None:
    statements = parse_policy_statements(policy_text)
    nodes: dict[tuple[str, str], str] = {}
    edges: set[tuple[str, str, str]] = set()

    def add_node(kind: str, value: Any, fill: str) -> str:
        key = (kind, str(value))
        if key not in nodes:
            node_id = f"{node_prefix}_n_{len(nodes)}"
            nodes[key] = node_id
            lines.append(
                f'"{node_id}" [label="{kind}\\n{escape_dot(value, 24)}", fillcolor="{fill}", fontcolor="white"];'
            )
        return nodes[key]

    def add_edge(source: str, rel_type: str, target: str) -> None:
        edge = (source, rel_type, target)
        if edge not in edges:
            edges.add(edge)
            lines.append(f'"{source}" -> "{target}" [label="{rel_type}"];')

    for statement in statements:
        effect = str(statement.get("Effect", "Allow")).strip()
        policy_rel = "DENIES" if effect == "Deny" else "ALLOWS"

        for action in values_as_list(statement.get("Action")):
            action_id = add_node("Action", action, BLUE)
            add_edge(policy_node_id, policy_rel, action_id)
            for resource in values_as_list(statement.get("Resource")):
                resource_id = add_node("Resource", resource, "#64748b")
                add_edge(action_id, "WORKS_ON", resource_id)
            for resource in values_as_list(statement.get("NotResource")):
                resource_id = add_node("NotResource", resource, "#334155")
                add_edge(action_id, "WORKS_NOT_ON", resource_id)

        for action in values_as_list(statement.get("NotAction")):
            action_id = add_node("NotAction", action, "#7c3aed")
            add_edge(policy_node_id, policy_rel, action_id)
            for resource in values_as_list(statement.get("Resource")):
                resource_id = add_node("Resource", resource, "#64748b")
                add_edge(action_id, "WORKS_NOT_ON", resource_id)
            for resource in values_as_list(statement.get("NotResource")):
                resource_id = add_node("NotResource", resource, "#334155")
                add_edge(action_id, "WORKS_NOT_ON", resource_id)


def build_neo4j_graph_dot(
    tables: dict[str, pd.DataFrame],
    selected_policy_names: list[str],
    lookup: dict[str, dict[str, Any]],
) -> str:
    policies = tables["policies"]
    lines = graph_header(width=6.4, height=3.2)

    for index, policy_name in enumerate(selected_policy_names):
        row = policies[policies["PolicyName"].astype(str) == policy_name]
        if row.empty:
            continue
        policy_row = row.iloc[0]
        policy_node_id = f"p_{index}"
        fill = policy_color(policy_name, lookup)
        score = lookup.get(policy_name, {}).get("anomaly_score")
        score_text = f"\\nscore {score:.3f}" if score is not None else ""
        lines.append(
            f'"{policy_node_id}" [label="Policy\\n{escape_dot(policy_name, 24)}{score_text}", fillcolor="{fill}", fontcolor="white"];'
        )
        try:
            add_policy_statement_edges(
                lines,
                policy_node_id,
                str(policy_row.get("PolicyObject", "")),
                f"policy_{index}",
            )
        except Exception as exc:
            error_id = f"parse_error_{index}"
            lines.append(f'"{error_id}" [label="parse error\\n{escape_dot(exc, 24)}", fillcolor="#7f1d1d", fontcolor="white"];')
            lines.append(f'"{policy_node_id}" -> "{error_id}";')

    lines.append("}")
    return "\n".join(lines)


def build_policy_graph_dot(policy_name: str, policy_text: str, color: str) -> str:
    try:
        parse_policy_statements(policy_text)
    except Exception as exc:
        return "\n".join(
            [
                "digraph G {",
                "rankdir=LR;",
                'graph [bgcolor="transparent", pad="0.08", nodesep="0.25", ranksep="0.40", margin="0", size="8,3!", ratio="compress"];',
                'node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=9, margin="0.08,0.05"];',
                'edge [color="#94a3b8", fontcolor="#cbd5e1", fontname="Helvetica", fontsize=8, arrowsize=0.6];',
                f'"policy" [label="{escape_dot(policy_name)}", fillcolor="{color}", fontcolor="white", style="rounded,filled"];',
                f'"error" [label="Parse error: {escape_dot(exc)}", fillcolor="#fee2e2", fontcolor="#7f1d1d", style="rounded,filled"];',
                '"policy" -> "error";',
                "}",
            ]
        )

    lines = [
        *graph_header(width=8, height=3),
        f'"policy" [label="{escape_dot(policy_name)}", fillcolor="{color}", fontcolor="white"];',
    ]

    add_policy_statement_edges(
        lines,
        "policy",
        policy_text,
        "inspect",
    )

    lines.append("}")
    return "\n".join(lines)


def learned_over_time_rows(snapshots: list[Path], label_names: set[str]) -> pd.DataFrame:
    seen_policies: set[str] = set()
    rows = []
    for path in snapshots:
        tables = load_snapshot(path.as_posix())
        names = set(tables["policies"]["PolicyName"].astype(str))
        seen_policies.update(names)
        risky_seen = seen_policies & label_names
        rows.append(
            {
                "snapshot": path.stem,
                "active_policies": len(names),
                "policies_learned": len(seen_policies),
                "training_candidates_learned": len(seen_policies) - len(risky_seen),
                "known_risky_seen": len(risky_seen),
            }
        )
    return pd.DataFrame(rows)


def selected_model_metric_rows(metrics: pd.DataFrame, model_name: str) -> pd.DataFrame:
    if metrics.empty:
        return pd.DataFrame()
    model_rows = metrics[metrics["model"].astype(str) == model_name]
    if model_rows.empty:
        return pd.DataFrame()

    row = model_rows.iloc[0]
    metric_names = ["precision", "recall", "f1", "roc_auc", "pr_auc"]
    display_names = {
        "precision": "Precision",
        "recall": "Recall",
        "f1": "F1",
        "roc_auc": "ROC-AUC",
        "pr_auc": "PR-AUC",
    }
    return pd.DataFrame(
        {
            "metric": [display_names[name] for name in metric_names],
            "value": [float(row[name]) for name in metric_names],
        }
    )


def main() -> None:
    st.set_page_config(page_title="IAM Anomaly Detection Demo", layout="wide")
    st.title("IAM Anomaly Detection Demo")

    all_snapshots = list_snapshots()
    if len(all_snapshots) < 5:
        st.error("Expected snapshots 0-4. Run `python -m src.pipeline simulate-updates` first.")
        return

    if "snapshot_4_added" not in st.session_state:
        st.session_state.snapshot_4_added = False

    label_names = get_label_names()
    update_report = load_update_report()

    st.sidebar.header("Demo Controls")
    model_name = st.sidebar.selectbox(
        "Saved prediction model",
        ["one_class_svm", "local_outlier_factor", "isolation_forest", "elliptic_envelope"],
        index=1,
    )
    metrics_dataset = st.sidebar.selectbox("Metric dataset", ["synth", "real", "merged"], index=0)

    predictions = load_predictions(model_name)
    lookup = prediction_lookup(predictions)
    shown_snapshots = visible_snapshots(all_snapshots)
    snapshot_path = current_snapshot(all_snapshots)
    snapshot_number = get_snapshot_number(snapshot_path)
    tables = load_snapshot(snapshot_path.as_posix())
    policies = tables["policies"]
    policy_names = sorted(policies["PolicyName"].astype(str).tolist())
    known_risky = set(policy_names) & label_names
    scored_rows = prediction_rows_for_snapshot(policies, predictions)
    risky_rows = scored_rows[scored_rows["y_pred"] == -1]

    st.caption("Snapshots are incremental IAM states. Each step can add, delete, or modify policies.")
    if not st.session_state.snapshot_4_added:
        if st.button("Apply next online update: snapshot 4", type="primary"):
            st.session_state.snapshot_4_added = True
            st.rerun()
    else:
        st.success("Snapshot 4 online update is now applied.")

    page1, page2, page3 = st.tabs(["1. Live Graph", "2. Learned Policies", "3. Model Stats"])

    with page1:
        st.subheader("Current IAM Health")
        st.write(update_text(snapshot_number, update_report))
        cols = st.columns(5)
        cols[0].metric("Current snapshot", snapshot_path.stem)
        cols[1].metric("Policies now", len(policy_names))
        cols[2].metric("Known risky labels", len(known_risky))
        cols[3].metric("Model-scored policies", len(scored_rows))
        cols[4].metric("Model-scored risky", len(risky_rows))

        st.subheader("Model Projection Graph")
        st.caption(
            "This matches the Node2Vec projection in config/model.yaml: Policy, Action, NotAction, Resource, NotResource "
            "with ALLOWS, DENIES, WORKS_ON, and WORKS_NOT_ON relationships. The demo view shows one risky policy and three safe policies."
        )
        graph_names = graph_policy_names_for_demo(scored_rows, policies)
        st.graphviz_chart(
            build_neo4j_graph_dot(tables, graph_names, lookup),
            use_container_width=False,
        )

        st.subheader("Inspect One Policy")
        if "selected_policy" not in st.session_state or st.session_state.selected_policy not in policy_names:
            if not risky_rows.empty:
                st.session_state.selected_policy = str(risky_rows.iloc[0]["policy_name"])
            else:
                st.session_state.selected_policy = policy_names[0]

        selected_policy = st.selectbox(
            "Policy",
            policy_names,
            index=policy_names.index(st.session_state.selected_policy),
        )
        st.session_state.selected_policy = selected_policy
        pred_row = lookup.get(selected_policy)
        policy_df = policies[policies["PolicyName"].astype(str) == selected_policy]
        policy_text = str(policy_df.iloc[0].get("PolicyObject", "")) if not policy_df.empty else ""
        status_cols = st.columns(3)
        status_cols[0].metric("Model status", policy_status(selected_policy, lookup))
        status_cols[1].metric("Prediction", pred_row["y_pred"] if pred_row else "1")
        status_cols[2].metric("Anomaly score", f"{pred_row['anomaly_score']:.8f}" if pred_row else "verified good")
        st.graphviz_chart(
            build_policy_graph_dot(selected_policy, policy_text, policy_color(selected_policy, lookup)),
            use_container_width=False,
        )

        with st.expander("Preview a new policy graph"):
            st.write("This only previews the graph. A new policy needs the full pipeline before the model can score it.")
            new_name = st.text_input("New policy name", value="demo-new-policy")
            new_text = st.text_area(
                "PolicyObject",
                value=repr([
                    {
                        "Effect": "Allow",
                        "Action": ["s3:GetObject"],
                        "Resource": ["arn:aws:s3:::demo-bucket/*"],
                    }
                ]),
                height=120,
            )
            if st.button("Preview graph"):
                st.graphviz_chart(build_policy_graph_dot(new_name, new_text, GREEN), use_container_width=False)

    with page2:
        st.subheader("Policies Learned Over Time")
        learned = learned_over_time_rows(shown_snapshots, label_names)
        st.caption("Active policies stay flat because each snapshot adds one policy and deletes one policy. Policies learned is cumulative, so it increases.")
        learned_chart = (
            alt.Chart(learned)
            .mark_line(point=True, strokeWidth=3)
            .encode(
                x=alt.X("snapshot:N", title="Snapshot", sort=None),
                y=alt.Y(
                    "policies_learned:Q",
                    title="Policies learned",
                    scale=alt.Scale(
                        domain=[
                            int(learned["policies_learned"].min()) - 1,
                            int(learned["policies_learned"].max()) + 1,
                        ]
                    ),
                ),
                tooltip=[
                    alt.Tooltip("snapshot:N", title="Snapshot"),
                    alt.Tooltip("policies_learned:Q", title="Policies learned"),
                ],
            )
            .properties(height=320)
        )
        st.altair_chart(learned_chart, use_container_width=True)
        st.dataframe(
            learned[["snapshot", "policies_learned", "training_candidates_learned", "known_risky_seen"]],
            use_container_width=True,
        )

        st.subheader("Top Risky Policies In Current Snapshot")
        if risky_rows.empty:
            st.info("No saved model predictions matched this snapshot.")
        else:
            st.dataframe(
                risky_rows[["policy_name", "y_pred", "anomaly_score"]].head(12),
                use_container_width=True,
            )

        st.subheader("Timeline Updates")
        steps = pd.DataFrame(update_report.get("step_reports", []))
        if not st.session_state.snapshot_4_added and not steps.empty:
            steps = steps[steps["step"] <= 3]
        st.dataframe(steps, use_container_width=True)

    with page3:
        st.subheader("Model Stats")
        metrics = load_metrics(metrics_dataset)
        if metrics.empty:
            st.info("No metrics file found.")
        else:
            chart_data = selected_model_metric_rows(metrics, model_name)
            if chart_data.empty:
                st.info(f"No metrics row found for {model_name}.")
            else:
                chart = (
                    alt.Chart(chart_data)
                    .mark_bar(size=46, cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
                    .encode(
                        x=alt.X("metric:N", title="", sort=None),
                        y=alt.Y("value:Q", title="Score", scale=alt.Scale(domain=[0, 1])),
                        color=alt.Color("metric:N", legend=None),
                        tooltip=[
                            alt.Tooltip("metric:N", title="Metric"),
                            alt.Tooltip("value:Q", title="Score", format=".3f"),
                        ],
                    )
                    .properties(height=320)
                )
                st.altair_chart(chart, use_container_width=True)
            st.dataframe(metrics, use_container_width=True)

        st.write("These stats come from saved model metric CSVs. The bars use a fixed 0-1 scale.")


if __name__ == "__main__":
    main()
