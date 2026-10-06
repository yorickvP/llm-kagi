# llm-kagi

[LLM](https://llm.datasette.io/) plugin for the [Kagi API](https://kagi.com/api/docs): web search and page extraction as **tools**, **fragments** and **CLI commands**.

## Installation

Install this plugin in the same environment as LLM:

```bash
llm install -e path/to/llm-kagi
```

## Configuration

Get an API key from the [Kagi API dashboard](https://kagi.com/api/keys), then set it:

```bash
llm keys set kagi
# Paste key here
```

Or use the `KAGI_API_KEY` environment variable.

## Usage

### Tools

The plugin provides two tools that let a model search the web and read pages:

- `kagi_search(query, workflow="search", limit=10)`: search the web. `workflow` can be `search`, `news`, `images`, `videos` or `podcasts`.
- `kagi_extract(urls)`: fetch one or more pages and return their content as markdown.

```bash
llm -T kagi_search -T kagi_extract 'What changed in the most recent LLM release?' --td
```

For more control, use the `Kagi` toolbox, which provides both tools (as `Kagi_search` and `Kagi_extract`) and accepts configuration:

```bash
# Only search specific sites
llm -T 'Kagi(sites=["docs.python.org", "peps.python.org"])' 'How do I use match statements?'

# Exclude sites, localize results, and include the full content of the top 3 results
llm -T 'Kagi(exclude_sites=["pinterest.com"], region="nl", extract=3)' 'Best bike repair shops in Utrecht'
```

Toolbox options:

- `lens_id`: a Kagi [lens](https://help.kagi.com/kagi/features/lenses.html) ID or shared lens URL
- `sites` / `exclude_sites`: lists of domains to restrict the search to, or exclude
- `region`: an ISO 3166-1 alpha-2 country code
- `safe_search`: `True` or `False`
- `extract`: replace the snippets of the top N (1-10) web results with the full page content. This is billed at the Extract API rate.

### Fragments

Use the `kagi:` fragment prefix to include a web page (as markdown) in a prompt:

```bash
llm -f kagi:https://simonwillison.net/2025/Apr/7/long-context-llm/ 'Summarize this'
```

Anything that isn't a URL is treated as a search query, and the results are included in the prompt:

```bash
llm -f 'kagi:llm fragments plugins' 'Which fragment plugins exist?'
```

### Commands

Search from the command line:

```bash
llm kagi search 'llm plugins' -n 5
llm kagi search 'rust 2024 edition' --workflow news --region nl
llm kagi search 'pelican bicycle' --site simonwillison.net --after 2025-01-01
llm kagi search 'sqlite json' --extract 2   # include full content of the top 2 results
llm kagi search 'steve jobs' --json         # raw API response
```

Extract pages as markdown:

```bash
llm kagi extract https://llm.datasette.io/en/stable/tools.html
llm kagi extract https://example.com/a https://example.com/b --json
```

Run `llm kagi search --help` for all options.

### Python API

```python
import llm_kagi

response = llm_kagi.search("llm plugins", limit=5)
print(llm_kagi.format_search_results(response, query="llm plugins"))

pages = llm_kagi.extract(["https://llm.datasette.io/"])
print(pages[0]["markdown"])
```

## Development

To set up this plugin locally, first check out the code. Then create a new virtual environment:

```bash
cd llm-kagi
python -m venv venv
source venv/bin/activate
```

Now install the dependencies and test dependencies:

```bash
llm install -e '.[test]'
```

To run the tests:

```bash
python -m pytest
```
