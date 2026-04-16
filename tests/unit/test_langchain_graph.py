"""Unit tests for CoordinodeGraph (langchain-coordinode).

All tests use mock clients — no proto stubs or running server required.
"""

from __future__ import annotations

from typing import Any

from langchain_coordinode import CoordinodeGraph

# ── Fake client helpers ───────────────────────────────────────────────────────


class _FakeTextResult:
    """Matches coordinode.client.TextResult shape."""

    def __init__(self, node_id: int, score: float, snippet: str = "") -> None:
        self.node_id = node_id
        self.score = score
        self.snippet = snippet


class _ClientWithTextSearch:
    """Minimal fake client that implements text_search()."""

    def __init__(self, results: list[_FakeTextResult]) -> None:
        self._results = results
        self.last_call: dict[str, Any] = {}

    def cypher(self, query: str, params: dict | None = None) -> list[dict]:
        return []

    def text_search(
        self,
        label: str,
        query: str,
        *,
        limit: int = 10,
        fuzzy: bool = False,
        language: str = "",
    ) -> list[_FakeTextResult]:
        self.last_call = {
            "label": label,
            "query": query,
            "limit": limit,
            "fuzzy": fuzzy,
            "language": language,
        }
        return self._results

    def close(self) -> None:
        # No-op: keeps interface parity with real CoordinodeClient.
        return None


class _ClientWithoutTextSearch:
    """Fake client that does NOT implement text_search (e.g. bare LocalClient)."""

    def cypher(self, query: str, params: dict | None = None) -> list[dict]:
        return []

    def close(self) -> None:
        # No-op: keeps interface parity with real CoordinodeClient.
        return None


class _ClientWithRaisingTextSearch:
    """Fake client whose text_search raises (e.g. gRPC UNIMPLEMENTED)."""

    def cypher(self, query: str, params: dict | None = None) -> list[dict]:
        return []

    def text_search(self, label: str, query: str, **kwargs: Any) -> list[Any]:
        raise RuntimeError("StatusCode.UNIMPLEMENTED")

    def close(self) -> None:
        # No-op: keeps interface parity with real CoordinodeClient.
        return None


# ── Tests: keyword_search ─────────────────────────────────────────────────────


class TestKeywordSearch:
    def test_returns_list_of_dicts(self) -> None:
        """keyword_search returns list[dict] with id/score/snippet keys."""
        results = [
            _FakeTextResult(node_id=1, score=0.95, snippet="<b>machine</b> learning"),
            _FakeTextResult(node_id=2, score=0.72, snippet=""),
        ]
        client = _ClientWithTextSearch(results)
        graph = CoordinodeGraph(client=client)

        out = graph.keyword_search("machine learning", k=5, label="Article")

        assert len(out) == 2
        assert out[0] == {"id": 1, "score": 0.95, "snippet": "<b>machine</b> learning"}
        assert out[1] == {"id": 2, "score": 0.72, "snippet": ""}

    def test_passes_params_to_client(self) -> None:
        """keyword_search forwards label, query, k, fuzzy, language to client.text_search."""
        client = _ClientWithTextSearch([])
        graph = CoordinodeGraph(client=client)

        graph.keyword_search(
            "deep learning",
            k=3,
            label="Paper",
            fuzzy=True,
            language="english",
        )

        assert client.last_call == {
            "label": "Paper",
            "query": "deep learning",
            "limit": 3,
            "fuzzy": True,
            "language": "english",
        }

    def test_default_label_is_chunk(self) -> None:
        """Default label is 'Chunk' (mirrors similarity_search default)."""
        client = _ClientWithTextSearch([])
        graph = CoordinodeGraph(client=client)

        graph.keyword_search("query")

        assert client.last_call["label"] == "Chunk"

    def test_default_k_is_5(self) -> None:
        """Default k is 5."""
        client = _ClientWithTextSearch([])
        graph = CoordinodeGraph(client=client)

        graph.keyword_search("query")

        assert client.last_call["limit"] == 5

    def test_returns_empty_for_client_without_text_search(self) -> None:
        """Returns [] gracefully when the injected client has no text_search method."""
        client = _ClientWithoutTextSearch()
        graph = CoordinodeGraph(client=client)

        out = graph.keyword_search("query")

        assert out == []

    def test_returns_empty_list_when_no_results(self) -> None:
        """Returns [] when text_search returns no results (e.g. no matching index)."""
        client = _ClientWithTextSearch([])
        graph = CoordinodeGraph(client=client)

        out = graph.keyword_search("no match", label="Ghost")

        assert out == []

    def test_empty_snippet_preserved(self) -> None:
        """snippet key is always present even when the server returns empty string."""
        results = [_FakeTextResult(node_id=42, score=0.5)]  # snippet defaults to ""
        client = _ClientWithTextSearch(results)
        graph = CoordinodeGraph(client=client)

        out = graph.keyword_search("test")

        assert out[0]["snippet"] == ""

    def test_returns_empty_when_text_search_raises(self) -> None:
        """Returns [] when text_search raises (e.g. gRPC UNIMPLEMENTED from older server)."""
        client = _ClientWithRaisingTextSearch()
        graph = CoordinodeGraph(client=client)

        out = graph.keyword_search("query")

        assert out == []
