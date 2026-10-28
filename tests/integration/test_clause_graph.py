"""Clause-tree graph operations on the live Neo4j, under an act id no real act has."""

from collections.abc import Iterator

import pytest

from opensansad.graph.clauses import GenealogyEdgesExist, GraphNode, delete_tree, replace_tree
from opensansad.graph.client import GraphClient

pytestmark = pytest.mark.integration

ACT, VERSION = -1, "integration-test"
NODES = [
    GraphNode("pt1", None, "part", "I", "TESTING", 0),
    GraphNode("s1", "pt1", "section", "1", "Short title.", 1),
    GraphNode("s1.ss1", "s1", "subsection", "1", None, 2),
    GraphNode("s1.ss1.cl(a)", "s1.ss1", "clause", "a", None, 3),
]


@pytest.fixture
def graph() -> Iterator[GraphClient]:
    with GraphClient.from_settings() as client:
        delete_tree(client, ACT, VERSION)
        delete_tree(client, ACT, VERSION + "-later")
        yield client
        delete_tree(client, ACT, VERSION)
        delete_tree(client, ACT, VERSION + "-later")


def chain(graph: GraphClient, path: str) -> list[str]:
    records, _, _ = graph.driver.execute_query(
        "MATCH p=(c:Clause {act_id: $act, version_label: $v, path: $path})-[:CHILD_OF*0..]->(r) "
        "WHERE NOT (r)-[:CHILD_OF]->() RETURN [n IN nodes(p) | n.path] AS chain",
        act=ACT, v=VERSION, path=path)
    return records[0]["chain"]


def test_replace_tree_is_idempotent(graph: GraphClient) -> None:
    assert replace_tree(graph, ACT, VERSION, NODES) == 3
    assert replace_tree(graph, ACT, VERSION, NODES) == 3
    assert chain(graph, "s1.ss1.cl(a)") == ["s1.ss1.cl(a)", "s1.ss1", "s1", "pt1"]
    records, _, _ = graph.driver.execute_query(
        "MATCH (c:Clause {act_id: $act, version_label: $v}) RETURN count(c) AS n",
        act=ACT, v=VERSION)
    assert records[0]["n"] == 4


def test_genealogy_edges_block_a_rebuild(graph: GraphClient) -> None:
    replace_tree(graph, ACT, VERSION, NODES)
    replace_tree(graph, ACT, VERSION + "-later", NODES)
    graph.driver.execute_query(
        "MATCH (a:Clause {act_id: $act, version_label: $later, path: 's1'}), "
        "(b:Clause {act_id: $act, version_label: $v, path: 's1'}) "
        "CREATE (a)-[:DESCENDS_FROM {op_type: 'substitute'}]->(b)",
        act=ACT, v=VERSION, later=VERSION + "-later")
    with pytest.raises(GenealogyEdgesExist):
        replace_tree(graph, ACT, VERSION, NODES)
    assert replace_tree(graph, ACT, VERSION, NODES, force=True) == 3
