"""Clause trees in Neo4j: `(:Clause {act_id, version_label, path})` nodes, `CHILD_OF` edges.

Sections and Schedules are `CHILD_OF` their Part / Chapter, so the graph keeps the printed
structure the Postgres `parent_path` records. Genealogy edges (Phase 2: `DESCENDS_FROM`)
connect clauses of different versions; a version that has any is not rebuilt without `force`.
"""

from dataclasses import dataclass

from opensansad.graph.client import GraphClient

CONSTRAINT = (
    "CREATE CONSTRAINT clause_key IF NOT EXISTS FOR (c:Clause) "
    "REQUIRE (c.act_id, c.version_label, c.path) IS UNIQUE")


class GenealogyEdgesExist(RuntimeError):
    pass


@dataclass
class GraphNode:
    path: str
    parent_path: str | None
    node_type: str
    number: str | None
    heading: str | None
    ord: int


def other_edges(client: GraphClient, act_id: int, version_label: str) -> int:
    """Relationships other than CHILD_OF touching this version's clauses."""
    records, _, _ = client.driver.execute_query(
        "MATCH (c:Clause {act_id: $act, version_label: $version})-[r]-() "
        "WHERE type(r) <> 'CHILD_OF' RETURN count(r) AS n",
        act=act_id, version=version_label)
    return records[0]["n"]


def replace_tree(client: GraphClient, act_id: int, version_label: str,
                 nodes: list[GraphNode], *, force: bool = False) -> int:
    """Replace one version's clause tree; returns the number of CHILD_OF edges written."""
    client.driver.execute_query(CONSTRAINT)
    if not force and (n := other_edges(client, act_id, version_label)):
        raise GenealogyEdgesExist(
            f"{n} genealogy edges touch act {act_id} version {version_label}; use force")
    rows = [vars(n) for n in nodes]
    with client.driver.session() as session:
        def write(tx) -> int:
            tx.run("MATCH (c:Clause {act_id: $act, version_label: $version}) DETACH DELETE c",
                   act=act_id, version=version_label)
            tx.run("UNWIND $rows AS r CREATE (c:Clause {act_id: $act, version_label: $version, "
                   "path: r.path, node_type: r.node_type, number: r.number, heading: r.heading, "
                   "ord: r.ord})", rows=rows, act=act_id, version=version_label)
            result = tx.run(
                "UNWIND $rows AS r WITH r WHERE r.parent_path IS NOT NULL "
                "MATCH (c:Clause {act_id: $act, version_label: $version, path: r.path}) "
                "MATCH (p:Clause {act_id: $act, version_label: $version, path: r.parent_path}) "
                "CREATE (c)-[:CHILD_OF]->(p) RETURN count(*) AS n",
                rows=rows, act=act_id, version=version_label)
            return result.single()["n"]
        edges = session.execute_write(write)
    expected = sum(n.parent_path is not None for n in nodes)
    if edges != expected:
        raise RuntimeError(f"{edges} CHILD_OF edges written, {expected} expected")
    return edges


def delete_tree(client: GraphClient, act_id: int, version_label: str) -> None:
    client.driver.execute_query(
        "MATCH (c:Clause {act_id: $act, version_label: $version}) DETACH DELETE c",
        act=act_id, version=version_label)
