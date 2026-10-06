import json

import llm
import pytest
from click.testing import CliRunner
from llm.cli import cli

import llm_kagi

SEARCH_URL = "https://kagi.com/api/v1/search"
EXTRACT_URL = "https://kagi.com/api/v1/extract"
META = {"trace": "abc123", "ms": 100, "node": "us-central1"}

SEARCH_RESPONSE = {
    "meta": META,
    "data": {
        "search": [
            {
                "url": "https://github.com/simonw/llm-plugins",
                "title": "simonw/llm-plugins: The LLM plugins directory",
                "snippet": "The LLM <strong>plugins</strong> directory &amp; more. Simon&#39;s list.",
                "time": "2023-08-21T20:31:02Z",
                "props": {"paywalled": True},
            },
            {
                "url": "https://llm.datasette.io/",
                "title": "LLM &amp; friends",
                "props": {"ai_generated": True},
            },
        ],
        "adjacent_question": [
            {
                "url": "https://example.com/q",
                "title": "Question page",
                "props": {"question": "What&#39;s an LLM plugin?"},
            }
        ],
        "video": [],
        "brand_new_category": [{"url": "https://example.com/new", "title": "New"}],
    },
}


@pytest.fixture(autouse=True)
def api_key(monkeypatch, tmpdir):
    monkeypatch.setenv("LLM_USER_PATH", str(tmpdir))
    monkeypatch.setenv("KAGI_API_KEY", "test-key")


def test_plugin_is_installed():
    runner = CliRunner()
    result = runner.invoke(cli, ["plugins"])
    assert result.exit_code == 0
    names = [plugin["name"] for plugin in json.loads(result.output)]
    assert "llm-kagi" in names


def test_tools_registered():
    tools = llm.get_tools()
    assert tools["kagi_search"].name == "kagi_search"
    assert tools["kagi_extract"].name == "kagi_extract"
    assert tools["Kagi"] is llm_kagi.Kagi
    schema = tools["kagi_search"].input_schema
    assert schema["required"] == ["query"]
    assert schema["properties"]["workflow"]["enum"] == list(llm_kagi.WORKFLOWS)


def test_format_search_results():
    output = llm_kagi.format_search_results(SEARCH_RESPONSE, query="llm plugins")
    assert output == (
        "# Kagi search results for: llm plugins\n\n"
        "## Web results\n\n"
        "### simonw/llm-plugins: The LLM plugins directory\n"
        "https://github.com/simonw/llm-plugins\n"
        "Published: 2023-08-21 · Paywalled\n\n"
        "The LLM plugins directory & more. Simon's list.\n\n"
        "### LLM & friends\n"
        "https://llm.datasette.io/\n"
        "AI-generated\n\n"
        "## Related questions\n\n"
        "### Question page\n"
        "https://example.com/q\n"
        "Question: What's an LLM plugin?\n\n"
        "## Brand new category\n\n"
        "### New\n"
        "https://example.com/new"
    )


def test_format_search_results_keeps_extracted_content():
    markdown = "# Docs\n\nUse `html.escape('<b>&amp;</b>')`"
    response = {
        "meta": META,
        "data": {
            "search": [
                {"url": "https://a.example", "title": "A", "snippet": markdown},
                {"url": "https://b.example", "title": "B", "snippet": "<b>b</b> &amp;"},
            ]
        },
    }
    output = llm_kagi.format_search_results(response, extracted=1)
    assert markdown in output
    assert output.endswith("b &")


def test_format_search_results_empty():
    output = llm_kagi.format_search_results({"meta": META, "data": {}}, query="x")
    assert output == "# Kagi search results for: x\n\nNo results found."


def test_kagi_search_tool(httpx_mock):
    httpx_mock.add_response(url=SEARCH_URL, method="POST", json=SEARCH_RESPONSE)
    output = llm_kagi.kagi_search("llm plugins", workflow="news", limit=5)
    assert output.startswith("# Kagi search results for: llm plugins")
    request = httpx_mock.get_request()
    assert request.headers["authorization"] == "Bearer test-key"
    assert json.loads(request.content) == {
        "query": "llm plugins",
        "workflow": "news",
        "limit": 5,
    }


def test_toolbox_search(httpx_mock):
    httpx_mock.add_response(url=SEARCH_URL, method="POST", json=SEARCH_RESPONSE)
    toolbox = llm_kagi.Kagi(
        sites=["github.com"], exclude_sites=["example.com"], region="nl", extract=2
    )
    assert {tool.name for tool in toolbox.tools()} == {"Kagi_search", "Kagi_extract"}
    toolbox.search("llm plugins")
    assert json.loads(httpx_mock.get_request().content) == {
        "query": "llm plugins site:github.com -site:example.com",
        "workflow": "search",
        "limit": 10,
        "filters": {"region": "nl"},
        "extract": {"count": 2},
    }


def test_kagi_extract_tool(httpx_mock):
    httpx_mock.add_response(
        url=EXTRACT_URL,
        method="POST",
        json={
            "meta": META,
            "data": [
                {"url": "https://a.example/", "markdown": "# A\n\nHello"},
                {"url": "https://b.example/", "error": "No data returned"},
            ],
        },
    )
    output = llm_kagi.kagi_extract(["https://a.example/", "https://b.example/"])
    assert output == (
        "# Content of https://a.example/\n\n# A\n\nHello\n\n---\n\n"
        "# Content of https://b.example/\n\nExtraction failed: No data returned"
    )
    assert json.loads(httpx_mock.get_request().content) == {
        "pages": [{"url": "https://a.example/"}, {"url": "https://b.example/"}]
    }


def test_extract_batches_requests(httpx_mock):
    urls = [f"https://example.com/{i}" for i in range(12)]
    for batch in (urls[:10], urls[10:]):
        httpx_mock.add_response(
            url=EXTRACT_URL,
            method="POST",
            match_json={"pages": [{"url": url} for url in batch]},
            json={"meta": META, "data": [{"url": u, "markdown": u} for u in batch]},
        )
    pages = llm_kagi.extract(urls)
    assert [page["url"] for page in pages] == urls


def test_api_error(httpx_mock):
    httpx_mock.add_response(
        url=SEARCH_URL,
        method="POST",
        status_code=400,
        json={
            "meta": META,
            "data": None,
            "errors": [{"code": "general.invalid_token", "message": "Bad token"}],
        },
    )
    with pytest.raises(llm_kagi.KagiError) as ex:
        llm_kagi.search("x")
    assert str(ex.value) == "Kagi API error (HTTP 400): Bad token [trace: abc123]"


def test_missing_key(monkeypatch):
    monkeypatch.delenv("KAGI_API_KEY")
    with pytest.raises(llm_kagi.KagiError, match="llm keys set kagi"):
        llm_kagi.search("x")


def test_stored_key(httpx_mock, monkeypatch):
    monkeypatch.delenv("KAGI_API_KEY")
    runner = CliRunner()
    runner.invoke(cli, ["keys", "set", "kagi", "--value", "stored-key"])
    httpx_mock.add_response(url=SEARCH_URL, method="POST", json=SEARCH_RESPONSE)
    llm_kagi.search("x")
    assert httpx_mock.get_request().headers["authorization"] == "Bearer stored-key"


def test_fragment_loader_url(httpx_mock):
    httpx_mock.add_response(
        url=EXTRACT_URL,
        method="POST",
        json={"meta": META, "data": [{"url": "https://a.example/", "markdown": "Hi"}]},
    )
    fragment = llm_kagi.kagi_fragment_loader("https://a.example/")
    assert str(fragment) == "Hi"
    assert fragment.source == "https://a.example/"


def test_fragment_loader_url_failure(httpx_mock):
    httpx_mock.add_response(
        url=EXTRACT_URL,
        method="POST",
        json={"meta": META, "data": [{"url": "https://a.example/", "error": "Nope"}]},
    )
    with pytest.raises(llm_kagi.KagiError, match="Nope"):
        llm_kagi.kagi_fragment_loader("https://a.example/")


def test_fragment_loader_search(httpx_mock):
    httpx_mock.add_response(url=SEARCH_URL, method="POST", json=SEARCH_RESPONSE)
    fragment = llm_kagi.kagi_fragment_loader("llm plugins")
    assert fragment.startswith("# Kagi search results for: llm plugins")
    assert fragment.source == "https://kagi.com/search?q=llm+plugins"


def test_cli_search(httpx_mock):
    httpx_mock.add_response(url=SEARCH_URL, method="POST", json=SEARCH_RESPONSE)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "kagi",
            "search",
            "llm",
            "plugins",
            "-n",
            "3",
            "--site",
            "github.com",
            "--site",
            "simonwillison.net",
            "--after",
            "2024-01-01",
            "--no-safe-search",
            "--extract",
            "1",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.output.startswith("# Kagi search results for: llm plugins")
    assert json.loads(httpx_mock.get_request().content) == {
        "query": "llm plugins (site:github.com OR site:simonwillison.net)",
        "limit": 3,
        "filters": {"after": "2024-01-01"},
        "extract": {"count": 1},
        "safe_search": False,
    }


def test_cli_search_json(httpx_mock):
    httpx_mock.add_response(url=SEARCH_URL, method="POST", json=SEARCH_RESPONSE)
    runner = CliRunner()
    result = runner.invoke(cli, ["kagi", "search", "x", "--json", "--key", "k2"])
    assert result.exit_code == 0
    assert json.loads(result.output) == SEARCH_RESPONSE
    assert httpx_mock.get_request().headers["authorization"] == "Bearer k2"


def test_cli_search_error(httpx_mock):
    httpx_mock.add_response(
        url=SEARCH_URL,
        method="POST",
        status_code=429,
        json={"meta": META, "errors": [{"code": "x", "message": "Slow down"}]},
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["kagi", "search", "x"])
    assert result.exit_code == 1
    assert "Error: Kagi API error (HTTP 429): Slow down" in result.output


def test_cli_extract(httpx_mock):
    httpx_mock.add_response(
        url=EXTRACT_URL,
        method="POST",
        json={"meta": META, "data": [{"url": "https://a.example/", "markdown": "Hi"}]},
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["kagi", "extract", "https://a.example/"])
    assert result.exit_code == 0
    assert result.output == "Hi\n"


def test_cli_extract_failure(httpx_mock):
    httpx_mock.add_response(
        url=EXTRACT_URL,
        method="POST",
        json={"meta": META, "data": [{"url": "https://a.example/", "error": "Nope"}]},
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["kagi", "extract", "https://a.example/"])
    assert result.exit_code == 1
    assert "Could not extract https://a.example/: Nope" in result.output
