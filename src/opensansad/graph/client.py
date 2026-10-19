from neo4j import Driver, GraphDatabase

from opensansad.config import get_settings


class GraphClient:
    """Thin wrapper around the Neo4j driver. Genealogy graph ops are added in later steps."""

    def __init__(self, uri: str, user: str, password: str) -> None:
        self._driver: Driver = GraphDatabase.driver(uri, auth=(user, password))

    @classmethod
    def from_settings(cls) -> "GraphClient":
        s = get_settings()
        return cls(s.neo4j_uri, s.neo4j_user, s.neo4j_password.get_secret_value())

    def ping(self) -> bool:
        self._driver.verify_connectivity()
        records, _, _ = self._driver.execute_query("RETURN 1 AS ok")
        return records[0]["ok"] == 1

    def close(self) -> None:
        self._driver.close()

    def __enter__(self) -> "GraphClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
