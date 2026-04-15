from __future__ import annotations

from typing import Any

import pandas as pd
from neo4j import GraphDatabase

from src.core.common import write_json
from src.core.data_ops import load_and_normalize_tables, parse_json_like_list


def create_verified_driver(neo4j_cfg: dict[str, Any], stage: str = "pipeline") -> Any:
    """Create a Neo4j driver and fail fast with an actionable error."""
    uri = neo4j_cfg["uri"]
    username = neo4j_cfg["username"]
    password = neo4j_cfg["password"]
    database = neo4j_cfg.get("database", "neo4j")

    driver = GraphDatabase.driver(uri, auth=(username, password))
    try:
        driver.verify_connectivity()
        with driver.session(database=database) as session:
            session.run("RETURN 1 AS ok").single()
        return driver
    except Exception as exc:
        driver.close()
        raise RuntimeError(
            f"[{stage}] Neo4j connection failed.\n"
            f"Configured URI: {uri}\n"
            f"Configured database: {database}\n"
            f"Configured username: {username}\n"
            "Fix:\n"
            "1. Start Neo4j and ensure Bolt is available on the configured host/port.\n"
            "2. Verify credentials in config/neo4j.yaml.\n"
            "3. Verify the target database exists.\n"
            f"Original error: {exc}"
        ) from exc


def _run_query(
    driver: Any,
    query: str,
    parameters: dict[str, Any] | None = None,
    database: str = "neo4j",
) -> list[dict[str, Any]]:
    """Run a Cypher query and return list-of-dict records."""
    with driver.session(database=database) as session:
        result = session.run(query, parameters or {})
        return [dict(record) for record in result]


def _as_list(value: Any) -> list[Any]:
    """Normalize scalar/list values to list."""
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    return [value]


def _split_csv_tokens(value: Any) -> list[str]:
    """Split comma-separated synthetic export fields into stable tokens."""
    if value is None:
        return []
    text = str(value).strip()
    if not text:
        return []
    return [token.strip() for token in text.split(",") if token.strip()]


def _extract_policy_refs(value: Any) -> list[str]:
    """Handle both JSON-like policy attachments and synthetic CSV policy ids."""
    parsed = parse_json_like_list(value)
    refs: list[str] = []
    for entry in parsed:
        policy_name = str(entry.get("PolicyName", "")).strip()
        policy_arn = str(entry.get("PolicyArn", "")).strip()
        if policy_name:
            refs.append(policy_name)
        elif policy_arn:
            refs.append(policy_arn)
    if refs:
        return refs
    return _split_csv_tokens(value)


def _extract_user_refs(value: Any) -> list[str]:
    """Handle both JSON-like group members and synthetic CSV user ids."""
    parsed = parse_json_like_list(value)
    refs: list[str] = []
    for entry in parsed:
        user_name = str(entry.get("UserName", "")).strip()
        user_id = str(entry.get("UserId", "")).strip()
        if user_name:
            refs.append(user_name)
        elif user_id:
            refs.append(user_id)
    if refs:
        return refs
    return _split_csv_tokens(value)


def policy_key_from_row(row: pd.Series) -> str:
    """Create a stable policy key used across graph nodes and updates."""
    policy_id = str(row.get("PolicyId", "")).strip()
    if policy_id:
        return policy_id
    return str(row.get("PolicyName", "")).strip()


def _clear_graph(driver: Any, database: str) -> None:
    """Delete all graph data."""
    _run_query(driver, "MATCH (n) DETACH DELETE n", database=database)


def _create_schema(driver: Any, database: str) -> None:
    """Create uniqueness constraints for principal node keys."""
    statements = [
        "CREATE CONSTRAINT policy_key IF NOT EXISTS FOR (p:Policy) REQUIRE p.key IS UNIQUE",
        "CREATE CONSTRAINT user_key IF NOT EXISTS FOR (u:User) REQUIRE u.key IS UNIQUE",
        "CREATE CONSTRAINT group_key IF NOT EXISTS FOR (g:Group) REQUIRE g.key IS UNIQUE",
        "CREATE CONSTRAINT role_key IF NOT EXISTS FOR (r:Role) REQUIRE r.key IS UNIQUE",
    ]
    for stmt in statements:
        _run_query(driver, stmt, database=database)


def _create_policy_nodes(driver: Any, policies: pd.DataFrame, database: str) -> None:
    """Create/refresh Policy nodes."""
    query = """
    MERGE (p:Policy {key: $key})
    SET p.name = $name,
        p.id = $id,
        p.arn = $arn,
        p.policyObject = $policy_object
    """
    with driver.session(database=database) as session:
        for _, row in policies.iterrows():
            session.run(
                query,
                {
                    "key": policy_key_from_row(row),
                    "name": str(row.get("PolicyName", "")),
                    "id": str(row.get("PolicyId", "")),
                    "arn": str(row.get("Arn", "")),
                    "policy_object": str(row.get("PolicyObject", "")),
                },
            )


def _create_policy_statement_subgraph(driver: Any, policies: pd.DataFrame, database: str) -> None:
    """Create Action/NotAction and Resource/NotResource subgraph for each parsed policy.

    Uses ALLOWS/DENIES edges between Policy and Action based on the
    statement Effect field, matching the paper's graph schema.
    """
    # Query templates — %s is replaced with ALLOWS or DENIES at runtime.
    # Each query creates the action/resource nodes and both edges in one shot.
    action_resource_query = """
    MATCH (p:Policy {key: $policy_key})
    MERGE (a:Action {name: $action, policyKey: $policy_key})
    MERGE (p)-[:%s]->(a)
    MERGE (r:Resource {name: $resource, policyKey: $policy_key})
    MERGE (a)-[:WORKS_ON]->(r)
    """
    action_not_resource_query = """
    MATCH (p:Policy {key: $policy_key})
    MERGE (a:Action {name: $action, policyKey: $policy_key})
    MERGE (p)-[:%s]->(a)
    MERGE (r:NotResource {name: $resource, policyKey: $policy_key})
    MERGE (a)-[:WORKS_NOT_ON]->(r)
    """
    not_action_resource_query = """
    MATCH (p:Policy {key: $policy_key})
    MERGE (a:NotAction {name: $action, policyKey: $policy_key})
    MERGE (p)-[:%s]->(a)
    MERGE (r:Resource {name: $resource, policyKey: $policy_key})
    MERGE (a)-[:WORKS_NOT_ON]->(r)
    """
    not_action_not_resource_query = """
    MATCH (p:Policy {key: $policy_key})
    MERGE (a:NotAction {name: $action, policyKey: $policy_key})
    MERGE (p)-[:%s]->(a)
    MERGE (r:NotResource {name: $resource, policyKey: $policy_key})
    MERGE (a)-[:WORKS_NOT_ON]->(r)
    """

    with driver.session(database=database) as session:
        for _, row in policies.iterrows():
            if not bool(row.get("_policy_parse_ok", False)):
                continue

            policy_key = policy_key_from_row(row)
            statements = row.get("_policy_statements", [])
            if not isinstance(statements, list):
                continue

            for statement in statements:
                if not isinstance(statement, dict):
                    continue

                effect = str(statement.get("Effect", "Allow")).strip()
                rel_type = "DENIES" if effect == "Deny" else "ALLOWS"

                actions = _as_list(statement.get("Action"))
                not_actions = _as_list(statement.get("NotAction"))
                resources = _as_list(statement.get("Resource"))
                not_resources = _as_list(statement.get("NotResource"))

                params_base = {"policy_key": policy_key}
                for action in actions:
                    for resource in resources:
                        session.run(action_resource_query % rel_type, {**params_base, "action": str(action), "resource": str(resource)})
                for action in actions:
                    for resource in not_resources:
                        session.run(action_not_resource_query % rel_type, {**params_base, "action": str(action), "resource": str(resource)})
                for action in not_actions:
                    for resource in resources:
                        session.run(not_action_resource_query % rel_type, {**params_base, "action": str(action), "resource": str(resource)})
                for action in not_actions:
                    for resource in not_resources:
                        session.run(not_action_not_resource_query % rel_type, {**params_base, "action": str(action), "resource": str(resource)})


def _create_principal_subgraph(
    driver: Any,
    users: pd.DataFrame,
    groups: pd.DataFrame,
    roles: pd.DataFrame,
    database: str,
) -> None:
    """Create User/Group/Role nodes and policy attachment edges."""
    user_query = "MERGE (u:User {key: $key}) SET u.name = $name, u.id = $id, u.arn = $arn"
    group_query = "MERGE (g:Group {key: $key}) SET g.name = $name, g.id = $id, g.arn = $arn"
    role_query = "MERGE (r:Role {key: $key}) SET r.name = $name, r.id = $id, r.arn = $arn"
    user_to_policy_query = """
    MATCH (u:User {key: $user_key})
    MATCH (p:Policy)
    WHERE p.key = $policy_ref OR p.id = $policy_ref OR p.name = $policy_ref OR p.arn = $policy_ref
    MERGE (u)-[:ATTACHED_TO]->(p)
    """
    group_to_policy_query = """
    MATCH (g:Group {key: $group_key})
    MATCH (p:Policy)
    WHERE p.key = $policy_ref OR p.id = $policy_ref OR p.name = $policy_ref OR p.arn = $policy_ref
    MERGE (g)-[:ATTACHED_TO]->(p)
    """
    role_to_policy_query = """
    MATCH (r:Role {key: $role_key})
    MATCH (p:Policy)
    WHERE p.key = $policy_ref OR p.id = $policy_ref OR p.name = $policy_ref OR p.arn = $policy_ref
    MERGE (r)-[:ATTACHED_TO]->(p)
    """
    user_to_group_query = """
    MATCH (u:User)
    WHERE u.key = $user_ref OR u.id = $user_ref OR u.name = $user_ref OR u.arn = $user_ref
    MATCH (g:Group {key: $group_key})
    MERGE (u)-[:PART_OF]->(g)
    """

    with driver.session(database=database) as session:
        for _, row in users.iterrows():
            key = str(row.get("UserId", "")).strip() or str(row.get("UserName", "")).strip()
            session.run(user_query, {"key": key, "name": str(row.get("UserName", "")), "id": str(row.get("UserId", "")), "arn": str(row.get("Arn", ""))})
            for policy_ref in _extract_policy_refs(row.get("AttachedPolicies", "")):
                session.run(user_to_policy_query, {"user_key": key, "policy_ref": policy_ref})

        for _, row in groups.iterrows():
            key = str(row.get("GroupId", "")).strip() or str(row.get("GroupName", "")).strip()
            session.run(group_query, {"key": key, "name": str(row.get("GroupName", "")), "id": str(row.get("GroupId", "")), "arn": str(row.get("Arn", ""))})
            for policy_ref in _extract_policy_refs(row.get("AttachedPolicies", "")):
                session.run(group_to_policy_query, {"group_key": key, "policy_ref": policy_ref})
            for user_ref in _extract_user_refs(row.get("Users", "")):
                session.run(user_to_group_query, {"user_ref": user_ref, "group_key": key})

        for _, row in roles.iterrows():
            key = str(row.get("RoleId", "")).strip() or str(row.get("RoleName", "")).strip()
            session.run(role_query, {"key": key, "name": str(row.get("RoleName", "")), "id": str(row.get("RoleId", "")), "arn": str(row.get("Arn", ""))})
            for policy_ref in _extract_policy_refs(row.get("AttachedPolicies", "")):
                session.run(role_to_policy_query, {"role_key": key, "policy_ref": policy_ref})


def graph_quality_report(driver: Any, database: str, output_path: str) -> dict[str, Any]:
    """Count labels and relationship types for post-load sanity checks."""
    labels = ["Policy", "Action", "NotAction", "Resource", "NotResource", "User", "Group", "Role"]
    rels = ["ALLOWS", "DENIES", "WORKS_ON", "WORKS_NOT_ON", "ATTACHED_TO", "PART_OF"]
    node_counts: dict[str, int] = {}
    rel_counts: dict[str, int] = {}

    for label in labels:
        rows = _run_query(driver, "MATCH (n) WHERE $label IN labels(n) RETURN count(n) AS c", {"label": label}, database=database)
        node_counts[label] = int(rows[0]["c"]) if rows else 0
    for rel in rels:
        rows = _run_query(driver, "MATCH ()-[r]->() WHERE type(r) = $rel RETURN count(r) AS c", {"rel": rel}, database=database)
        rel_counts[rel] = int(rows[0]["c"]) if rows else 0

    report = {
        "nodes": node_counts,
        "relationships": rel_counts,
        "valid_non_empty_core": bool(node_counts.get("Policy", 0) > 0 and (rel_counts.get("ALLOWS", 0) + rel_counts.get("DENIES", 0)) > 0),
    }
    write_json(output_path, report)
    return report


def build_graph(tables: dict[str, pd.DataFrame], neo4j_cfg: dict[str, Any], graph_counts_path: str) -> dict[str, Any]:
    """Build the IAM graph from normalized sheets."""
    database = neo4j_cfg.get("database", "neo4j")
    driver = create_verified_driver(neo4j_cfg, stage="graph_build")
    try:
        if bool(neo4j_cfg.get("clear_graph", False)):
            _clear_graph(driver, database=database)
        _create_schema(driver, database=database)
        _create_policy_nodes(driver, tables["policies"], database=database)
        _create_policy_statement_subgraph(driver, tables["policies"], database=database)
        _create_principal_subgraph(driver, tables["users"], tables["groups"], tables["roles"], database=database)
        return graph_quality_report(driver, database=database, output_path=graph_counts_path)
    finally:
        driver.close()


def run_node2vec(neo4j_cfg: dict[str, Any], embedding_cfg: dict[str, Any], report_path: str) -> dict[str, Any]:
    """Create Policy embeddings using GDS Node2Vec."""
    database = neo4j_cfg.get("database", "neo4j")
    write_property = embedding_cfg.get("write_property", "embeddingNode2vec")
    desired_relationship_types = embedding_cfg.get("relationship_types", ["CONTAINS", "WORKS_ON", "WORKS_NOT_ON"])
    embedding_dimension = int(embedding_cfg.get("embedding_dimension", 128))
    walk_length = int(embedding_cfg.get("walk_length", 5000))
    iterations = int(embedding_cfg.get("iterations", 100))
    random_seed = int(embedding_cfg.get("random_seed", 42))
    graph_name = embedding_cfg.get("projection_name", "policy_projection")

    driver = create_verified_driver(neo4j_cfg, stage="embed")
    try:
        # Drop stale projection if a previous run crashed before cleanup.
        try:
            _run_query(driver, "CALL gds.graph.drop($name, false) YIELD graphName RETURN graphName", {"name": graph_name}, database=database)
        except Exception:
            pass

        desired_labels = embedding_cfg.get(
            "node_labels",
            ["Policy", "Action", "NotAction", "Resource", "NotResource"],
        )
        # Only project labels that actually have nodes — GDS rejects empty labels.
        existing = {
            r["label"]
            for r in _run_query(driver, "CALL db.labels() YIELD label RETURN label", database=database)
        }
        node_labels = [lbl for lbl in desired_labels if lbl in existing]
        relationship_counts = {
            row["rel_type"]: int(row["count"])
            for row in _run_query(
                driver,
                """
                CALL db.relationshipTypes() YIELD relationshipType
                CALL (relationshipType) {
                  MATCH ()-[r]->()
                  WHERE type(r) = relationshipType
                  RETURN count(r) AS count
                }
                RETURN relationshipType AS rel_type, count
                """,
                database=database,
            )
        }
        relationship_types = [
            rel for rel in desired_relationship_types if relationship_counts.get(rel, 0) > 0
        ]
        if not node_labels:
            raise RuntimeError("No projected node labels with data were found for Node2Vec.")
        if not relationship_types:
            raise RuntimeError(
                "No configured relationship types with data were found for Node2Vec."
            )
        # GDS requires node labels and relationship config as Cypher literals,
        # not query parameters.  Build the projection query string directly.
        labels_literal = "[" + ", ".join(f"'{lbl}'" for lbl in node_labels) + "]"
        rel_entries = ", ".join(
            f"{rel.lower()}: {{type: '{rel}', orientation: 'NATURAL'}}"
            for rel in relationship_types
        )
        rel_literal = "{" + rel_entries + "}"
        project_query = (
            f"CALL gds.graph.project($name, {labels_literal}, {rel_literal}) "
            "YIELD graphName RETURN graphName"
        )
        _run_query(driver, project_query, {"name": graph_name}, database=database)

        rows = _run_query(
            driver,
            """
            CALL gds.node2vec.write($name, {
              embeddingDimension: $embedding_dimension,
              iterations: $iterations,
              walkLength: $walk_length,
              writeProperty: $write_property,
              randomSeed: $random_seed
            })
            YIELD nodeCount, nodePropertiesWritten, computeMillis
            RETURN nodeCount, nodePropertiesWritten, computeMillis
            """,
            {
                "name": graph_name,
                "embedding_dimension": embedding_dimension,
                "iterations": iterations,
                "walk_length": walk_length,
                "write_property": write_property,
                "random_seed": random_seed,
            },
            database=database,
        )

        try:
            _run_query(driver, "CALL gds.graph.drop($name, false) YIELD graphName RETURN graphName", {"name": graph_name}, database=database)
        except Exception:
            pass

        validation_rows = _run_query(
            driver,
            """
            MATCH (p:Policy)
            WITH count(p) AS total,
                 count(p[$property]) AS with_embedding,
                 [x IN collect(p[$property]) WHERE x IS NOT NULL][0] AS sample
            RETURN total, with_embedding, size(sample) AS dimension
            """,
            {"property": write_property},
            database=database,
        )
        validation = validation_rows[0] if validation_rows else {"total": 0, "with_embedding": 0, "dimension": 0}
        total = int(validation.get("total", 0))
        with_embedding = int(validation.get("with_embedding", 0))

        report = {
            "mode_used": "gds.node2vec.write",
            "write_property": write_property,
            "configured_dimension": embedding_dimension,
            "projected_node_labels": node_labels,
            "projected_relationship_types": relationship_types,
            "details": rows[0] if rows else {},
            "validation": {
                "total_policy_nodes": total,
                "policies_with_embedding": with_embedding,
                "coverage_ratio": (with_embedding / total) if total else 0.0,
                "detected_dimension": int(validation.get("dimension") or 0),
            },
        }
        write_json(report_path, report)
        return report
    finally:
        driver.close()


def _delete_policy_subgraph(driver: Any, policy_keys: list[str], database: str) -> None:
    """Delete policies and their action/resource subgraph."""
    query = """
    MATCH (p:Policy {key: $policy_key})
    OPTIONAL MATCH (p)-[:ALLOWS|DENIES]->(a)-[r]->(res)
    DETACH DELETE p, a, res
    """
    with driver.session(database=database) as session:
        for key in policy_keys:
            session.run(query, {"policy_key": key})


def _refresh_principals(driver: Any, users: pd.DataFrame, groups: pd.DataFrame, roles: pd.DataFrame, database: str) -> None:
    """Replace principal nodes and attachments with the latest snapshot state."""
    with driver.session(database=database) as session:
        session.run("MATCH (u:User) DETACH DELETE u")
        session.run("MATCH (g:Group) DETACH DELETE g")
        session.run("MATCH (r:Role) DETACH DELETE r")
    _create_principal_subgraph(driver, users, groups, roles, database=database)


def update_graph_from_snapshots(
    old_data_cfg: dict[str, Any],
    new_data_cfg: dict[str, Any],
    neo4j_cfg: dict[str, Any],
    model_cfg: dict[str, Any],
    update_report_path: str,
    graph_counts_path: str,
    embedding_report_path: str,
) -> dict[str, Any]:
    """Update an existing graph by diffing old/new data snapshots."""
    old_tables, _ = load_and_normalize_tables(old_data_cfg, "outputs/logs/old_schema_report.json", "outputs/logs/old_policy_parse_errors.csv")
    new_tables, _ = load_and_normalize_tables(new_data_cfg, "outputs/logs/new_schema_report.json", "outputs/logs/new_policy_parse_errors.csv")

    old_policies = old_tables["policies"].copy()
    new_policies = new_tables["policies"].copy()
    old_policies["key"] = old_policies.apply(policy_key_from_row, axis=1)
    new_policies["key"] = new_policies.apply(policy_key_from_row, axis=1)
    old_by_key = old_policies.set_index("key", drop=False)
    new_by_key = new_policies.set_index("key", drop=False)

    old_keys = set(old_by_key.index)
    new_keys = set(new_by_key.index)
    deleted_keys = sorted(old_keys - new_keys)
    added_keys = sorted(new_keys - old_keys)
    common_keys = sorted(old_keys & new_keys)

    metadata_changed_keys: list[str] = []
    doc_changed_keys: list[str] = []
    ignored_cols = {"PolicyObject", "_policy_statements", "_policy_parse_ok", "_policy_parse_error"}

    for key in common_keys:
        old_row = old_by_key.loc[key]
        new_row = new_by_key.loc[key]
        if str(old_row.get("PolicyObject", "")) != str(new_row.get("PolicyObject", "")):
            doc_changed_keys.append(key)
            continue
        metadata_cols = [col for col in new_policies.columns if col not in ignored_cols]
        if any(str(old_row.get(col, "")) != str(new_row.get(col, "")) for col in metadata_cols):
            metadata_changed_keys.append(key)

    database = neo4j_cfg.get("database", "neo4j")
    driver = create_verified_driver(neo4j_cfg, stage="update_graph")
    try:
        to_rebuild = sorted(set(added_keys + doc_changed_keys))
        _delete_policy_subgraph(driver, deleted_keys + doc_changed_keys, database=database)

        if to_rebuild:
            rebuild_df = new_by_key.loc[to_rebuild].reset_index(drop=True)
            _create_policy_nodes(driver, rebuild_df, database=database)
            _create_policy_statement_subgraph(driver, rebuild_df, database=database)

        if metadata_changed_keys:
            update_query = """
            MATCH (p:Policy {key: $key})
            SET p.name = $name, p.id = $id, p.arn = $arn
            """
            with driver.session(database=database) as session:
                for key in metadata_changed_keys:
                    row = new_by_key.loc[key]
                    session.run(update_query, {"key": key, "name": str(row.get("PolicyName", "")), "id": str(row.get("PolicyId", "")), "arn": str(row.get("Arn", ""))})

        _refresh_principals(driver, new_tables["users"], new_tables["groups"], new_tables["roles"], database=database)
        counts = graph_quality_report(driver, database=database, output_path=graph_counts_path)
    finally:
        driver.close()

    embed_report: dict[str, Any] = {}
    if bool(model_cfg.get("recompute_embeddings_after_update", True)):
        embed_report = run_node2vec(neo4j_cfg=neo4j_cfg, embedding_cfg=model_cfg.get("embedding", {}), report_path=embedding_report_path)

    report = {
        "deleted_policies": len(deleted_keys),
        "added_policies": len(added_keys),
        "document_changed_policies": len(doc_changed_keys),
        "metadata_changed_policies": len(metadata_changed_keys),
        "graph_counts": counts,
        "embedding_refreshed": bool(embed_report),
    }
    write_json(update_report_path, report)
    return report
