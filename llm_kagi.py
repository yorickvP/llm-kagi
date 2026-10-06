import html
import json
import re
import urllib.parse
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Literal, Optional

import click
import httpx
import llm

API_BASE = "https://kagi.com/api/v1"
KEY_ALIAS = "kagi"
KEY_ENV_VAR = "KAGI_API_KEY"
MAX_EXTRACT_PAGES = 10

Workflow = Literal["search", "images", "videos", "news", "podcasts"]
WORKFLOWS = ("search", "images", "videos", "news", "podcasts")

# Result categories in the order they should be displayed, with headings.
# Categories returned by the API that are not listed here are appended at the end.
SECTIONS = {
    "direct_answer": "Direct answer",
    "infobox": "Infobox",
    "search": "Web results",
    "news": "News",
    "interesting_news": "Interesting news",
    "video": "Videos",
    "image": "Images",
    "podcast": "Podcasts",
    "podcast_creator": "Podcast creators",
    "code": "Code",
    "public_records": "Public records",
    "weather": "Weather",
    "package_tracking": "Package tracking",
    "listicle": "Listicles",
    "interesting_finds": "Interesting finds (small web)",
    "web_archive": "Web archive",
    "adjacent_question": "Related questions",
    "related_search": "Related searches",
}

# Kagi highlights query terms in snippets with HTML tags and escapes entities
_HIGHLIGHT_RE = re.compile(r"</?(?:strong|b|em|i)>")

try:
    _USER_AGENT = f"llm-kagi/{version('llm-kagi')}"
except PackageNotFoundError:  # pragma: no cover
    _USER_AGENT = "llm-kagi"


class KagiError(Exception):
    pass


def get_api_key(explicit_key: Optional[str] = None) -> str:
    key = llm.get_key(
        explicit_key=explicit_key, key_alias=KEY_ALIAS, env_var=KEY_ENV_VAR
    )
    if not key:
        raise KagiError(
            "No Kagi API key found. Set one with 'llm keys set kagi' "
            f"or the {KEY_ENV_VAR} environment variable."
        )
    return key


def _post(path: str, body: dict, key: Optional[str] = None) -> dict:
    try:
        response = httpx.post(
            f"{API_BASE}{path}",
            json=body,
            headers={
                "Authorization": f"Bearer {get_api_key(key)}",
                "User-Agent": _USER_AGENT,
            },
            timeout=60.0,
        )
    except httpx.HTTPError as ex:
        raise KagiError(f"Kagi API request failed: {ex}") from ex
    try:
        data = response.json()
    except ValueError:
        raise KagiError(
            f"Kagi API returned HTTP {response.status_code}: {response.text[:500]}"
        )
    # The spec documents "error", the API currently returns "errors"
    errors = data.get("errors") or data.get("error") or []
    if response.is_error or (errors and not data.get("data")):
        messages = "; ".join(e.get("message") or e.get("code", "") for e in errors)
        trace = (data.get("meta") or {}).get("trace")
        raise KagiError(
            f"Kagi API error (HTTP {response.status_code}): "
            f"{messages or response.reason_phrase}"
            + (f" [trace: {trace}]" if trace else "")
        )
    return data


def search(
    query: str,
    *,
    workflow: Optional[str] = None,
    limit: Optional[int] = None,
    page: Optional[int] = None,
    lens_id: Optional[str] = None,
    sites: Optional[list[str]] = None,
    exclude_sites: Optional[list[str]] = None,
    region: Optional[str] = None,
    after: Optional[str] = None,
    before: Optional[str] = None,
    extract: Optional[int] = None,
    safe_search: Optional[bool] = None,
    timeout: Optional[float] = None,
    key: Optional[str] = None,
) -> dict:
    "Run a search using the Kagi Search API, returning the decoded JSON response"
    # The inline "lens" object (sites_included/sites_excluded) is currently
    # ignored by the API, so restrict sites using search operators instead
    terms = [query]
    if sites:
        operators = [f"site:{site}" for site in sites]
        terms.append(
            operators[0] if len(operators) == 1 else f"({' OR '.join(operators)})"
        )
    terms.extend(f"-site:{site}" for site in exclude_sites or [])
    body: dict[str, Any] = {"query": " ".join(terms)}
    if workflow:
        body["workflow"] = workflow
    if limit:
        body["limit"] = limit
    if page:
        body["page"] = page
    if lens_id:
        body["lens_id"] = lens_id
    filters = {}
    if region:
        filters["region"] = region
    if after:
        filters["after"] = after
    if before:
        filters["before"] = before
    if filters:
        body["filters"] = filters
    if extract:
        body["extract"] = {"count": extract}
    if safe_search is not None:
        body["safe_search"] = safe_search
    if timeout:
        body["timeout"] = timeout
    return _post("/search", body, key)


def extract(
    urls: list[str], *, timeout: Optional[float] = None, key: Optional[str] = None
) -> list[dict]:
    """
    Extract page content as markdown using the Kagi Extract API.

    Returns a list of {"url": ..., "markdown": ...} or {"url": ..., "error": ...} dicts.
    """
    pages = []
    for i in range(0, len(urls), MAX_EXTRACT_PAGES):
        body: dict[str, Any] = {
            "pages": [{"url": url} for url in urls[i : i + MAX_EXTRACT_PAGES]]
        }
        if timeout:
            body["timeout"] = timeout
        pages.extend(_post("/extract", body, key).get("data") or [])
    return pages


def _clean(text: str) -> str:
    return html.unescape(_HIGHLIGHT_RE.sub("", text)).strip()


def _format_result(category: str, result: dict, extracted: bool = False) -> str:
    title = _clean(result.get("title") or "") or result.get("url", "")
    lines = [f"### {title}"]
    if result.get("url"):
        lines.append(result["url"])
    meta = []
    if result.get("time"):
        meta.append(f"Published: {result['time'][:10]}")
    props = result.get("props") or {}
    if props.get("paywalled"):
        meta.append("Paywalled")
    if props.get("ai_generated"):
        meta.append("AI-generated")
    if meta:
        lines.append(" · ".join(meta))
    if props.get("question"):
        lines.append(f"Question: {_clean(props['question'])}")
    if category == "image" and (result.get("image") or {}).get("url"):
        lines.append(f"Image: {result['image']['url']}")
    snippet = result.get("snippet") or ""
    # Extracted page markdown is used as-is, highlighted snippets get cleaned up
    snippet = snippet.strip() if extracted else _clean(snippet)
    if snippet:
        lines.extend(["", snippet])
    return "\n".join(lines)


def format_search_results(
    response: dict, query: Optional[str] = None, extracted: int = 0
) -> str:
    """
    Format a Kagi search API response as markdown.

    ``extracted`` is the number of top web results whose snippet was replaced
    with the extracted page content.
    """
    data = response.get("data") or {}
    categories = [c for c in SECTIONS if data.get(c)]
    categories += [c for c in data if c not in SECTIONS and data[c]]
    parts = []
    if query:
        parts.append(f"# Kagi search results for: {query}")
    if not categories:
        parts.append("No results found.")
    for category in categories:
        heading = SECTIONS.get(category) or category.replace("_", " ").capitalize()
        parts.append(f"## {heading}")
        for i, result in enumerate(data[category]):
            parts.append(
                _format_result(
                    category, result, extracted=category == "search" and i < extracted
                )
            )
    return "\n\n".join(parts)


def format_extracted_pages(pages: list[dict]) -> str:
    parts = []
    for page in pages:
        if page.get("markdown"):
            parts.append(f"# Content of {page['url']}\n\n{page['markdown'].strip()}")
        else:
            reason = page.get("error") or "no content returned"
            parts.append(f"# Content of {page['url']}\n\nExtraction failed: {reason}")
    return "\n\n---\n\n".join(parts)


def kagi_search(query: str, workflow: Workflow = "search", limit: int = 10) -> str:
    """
    Search the web using Kagi. Returns results as markdown with titles, URLs,
    publication dates and snippets.

    workflow selects the kind of results: "search" for web pages (default),
    "news", "images", "videos" or "podcasts".
    limit is the maximum number of results to return per result type.

    Supports Kagi search operators such as site:example.com, "exact phrase",
    -excluded and filetype:pdf. Use kagi_extract to read the full content
    of a result.
    """
    return format_search_results(
        search(query, workflow=workflow, limit=limit), query=query
    )


def kagi_extract(urls: list[str]) -> str:
    """
    Fetch one or more web pages (HTTPS URLs) and return their content as markdown.
    """
    return format_extracted_pages(extract(urls))


class Kagi(llm.Toolbox):
    """
    Search the web and read web pages using Kagi.
    """

    def __init__(
        self,
        lens_id: Optional[str] = None,
        sites: Optional[list[str]] = None,
        exclude_sites: Optional[list[str]] = None,
        region: Optional[str] = None,
        safe_search: Optional[bool] = None,
        extract: int = 0,
    ):
        self.lens_id = lens_id
        self.sites = sites
        self.exclude_sites = exclude_sites
        self.region = region
        self.safe_search = safe_search
        self.extract_count = extract

    def search(self, query: str, workflow: Workflow = "search", limit: int = 10) -> str:
        """
        Search the web using Kagi. Returns results as markdown with titles, URLs,
        publication dates and snippets.

        workflow selects the kind of results: "search" for web pages (default),
        "news", "images", "videos" or "podcasts".
        limit is the maximum number of results to return per result type.

        Supports Kagi search operators such as "exact phrase", -excluded and
        filetype:pdf.
        """
        response = search(
            query,
            workflow=workflow,
            limit=limit,
            lens_id=self.lens_id,
            sites=self.sites,
            exclude_sites=self.exclude_sites,
            region=self.region,
            safe_search=self.safe_search,
            extract=self.extract_count,
        )
        return format_search_results(
            response, query=query, extracted=self.extract_count
        )

    def extract(self, urls: list[str]) -> str:
        """
        Fetch one or more web pages (HTTPS URLs) and return their content as markdown.
        """
        return format_extracted_pages(extract(urls))


def kagi_fragment_loader(argument: str) -> llm.Fragment:
    """
    Use Kagi to fetch a web page as markdown (kagi:https://...) or to
    search the web (kagi:search terms)
    """
    if argument.startswith(("https://", "http://")):
        page = extract([argument])[0]
        if not page.get("markdown"):
            raise KagiError(
                f"Could not extract {argument}: {page.get('error') or 'no content'}"
            )
        return llm.Fragment(page["markdown"], argument)
    response = search(argument)
    return llm.Fragment(
        format_search_results(response, query=argument),
        "https://kagi.com/search?" + urllib.parse.urlencode({"q": argument}),
    )


@llm.hookimpl
def register_tools(register):
    register(kagi_search)
    register(kagi_extract)
    register(Kagi)


@llm.hookimpl
def register_fragment_loaders(register):
    register("kagi", kagi_fragment_loader)


@llm.hookimpl
def register_commands(cli):
    @cli.group()
    def kagi():
        "Search the web and extract pages using the Kagi API"

    @kagi.command(name="search")
    @click.argument("query", nargs=-1, required=True)
    @click.option(
        "-w",
        "--workflow",
        type=click.Choice(WORKFLOWS),
        help="Type of results to return",
    )
    @click.option("-n", "--limit", type=click.IntRange(1, 1024), help="Max results")
    @click.option("--page", type=click.IntRange(1, 10), help="Page number (1-10)")
    @click.option("--lens", "lens_id", help="Lens ID or shared lens URL")
    @click.option("sites", "--site", multiple=True, help="Only search this domain")
    @click.option(
        "exclude_sites", "--exclude-site", multiple=True, help="Exclude this domain"
    )
    @click.option("--region", help="ISO 3166-1 alpha-2 country code, e.g. 'nl'")
    @click.option("--after", help="Only results published after YYYY-MM-DD")
    @click.option("--before", help="Only results published before YYYY-MM-DD")
    @click.option(
        "--extract",
        type=click.IntRange(1, 10),
        help="Replace snippets of the top N results with full page content",
    )
    @click.option(
        "--safe-search/--no-safe-search", default=None, help="Toggle safe search"
    )
    @click.option(
        "--timeout",
        type=click.FloatRange(0.5, 4),
        help="Seconds to allow for collecting results",
    )
    @click.option("json_", "--json", is_flag=True, help="Output raw JSON")
    @click.option("--key", help="Kagi API key to use")
    def search_command(query, json_, extract, **kwargs):
        """
        Search the web using Kagi

        Example:

        \b
            llm kagi search 'llm plugins' --site github.com -n 5
        """
        query = " ".join(query)
        try:
            response = search(query, extract=extract, **kwargs)
        except KagiError as ex:
            raise click.ClickException(str(ex))
        if json_:
            click.echo(json.dumps(response, indent=2))
        else:
            click.echo(
                format_search_results(response, query=query, extracted=extract or 0)
            )

    @kagi.command(name="extract")
    @click.argument("urls", nargs=-1, required=True)
    @click.option(
        "--timeout",
        type=click.FloatRange(0.5, 10),
        help="Seconds to allow for fetching all pages",
    )
    @click.option("json_", "--json", is_flag=True, help="Output raw JSON")
    @click.option("--key", help="Kagi API key to use")
    def extract_command(urls, timeout, json_, key):
        """
        Extract the content of web pages as markdown using Kagi

        Example:

        \b
            llm kagi extract https://example.com/article
        """
        try:
            pages = extract(list(urls), timeout=timeout, key=key)
        except KagiError as ex:
            raise click.ClickException(str(ex))
        if json_:
            click.echo(json.dumps(pages, indent=2))
        elif len(pages) == 1:
            if not pages[0].get("markdown"):
                raise click.ClickException(
                    f"Could not extract {pages[0]['url']}: "
                    + (pages[0].get("error") or "no content returned")
                )
            click.echo(pages[0]["markdown"])
        else:
            click.echo(format_extracted_pages(pages))
