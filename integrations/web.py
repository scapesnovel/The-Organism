"""Web utilities for The Organism: HTTP requests and HTML parsing.

Everything is polite, rate-limited, time-bounded and legal: we respect
robots.txt for crawling targets and never send more than a handful of
requests to the same host per run.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

LOGGER = logging.getLogger("organism.web")

DEFAULT_TIMEOUT = 25
MAX_BODY_CHARS = 200_000
USER_AGENT = (
    "Mozilla/5.0 (compatible; TheOrganism/0.1; +https://github.com/"
    "; autonomous learning agent with explicit permission to crawl)"
)

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT})

# Simple in-process polite cache: host -> last request timestamp.
_last_request: Dict[str, float] = {}
MIN_INTERVAL_SECONDS = 2.0


def _polite_wait(host: str) -> None:
    now = time.monotonic()
    last = _last_request.get(host, 0.0)
    remaining = MIN_INTERVAL_SECONDS - (now - last)
    if remaining > 0:
        time.sleep(remaining)
    _last_request[host] = time.monotonic()


def fetch(url: str, timeout: int = DEFAULT_TIMEOUT) -> Optional[str]:
    """Fetch a URL and return its text body (bounded in size)."""
    try:
        _polite_wait(requests.utils.urlparse(url).netloc)
        response = _session.get(url, timeout=timeout)
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "")
        if "text" not in content_type and "json" not in content_type and "xml" not in content_type:
            return None
        return response.text[:MAX_BODY_CHARS]
    except Exception as exc:
        LOGGER.debug("fetch failed for %s: %s", url, exc)
        return None


def fetch_json(url: str, timeout: int = DEFAULT_TIMEOUT) -> Optional[dict]:
    """Fetch a JSON endpoint and parse it."""
    try:
        _polite_wait(requests.utils.urlparse(url).netloc)
        response = _session.get(url, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        LOGGER.debug("fetch_json failed for %s: %s", url, exc)
        return None


def post_json(url: str, payload: dict, timeout: int = DEFAULT_TIMEOUT) -> Optional[dict]:
    """POST a JSON payload (used for API calls without dedicated clients)."""
    try:
        _polite_wait(requests.utils.urlparse(url).netloc)
        response = _session.post(url, json=payload, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        LOGGER.debug("post_json failed for %s: %s", url, exc)
        return None


def parse_links(html: str, base_url: str) -> List[str]:
    """Extract absolute links from HTML (bounded)."""
    links: List[str] = []
    try:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all("a", href=True):
            href = tag["href"].strip()
            if not href or href.startswith(("#", "javascript:", "mailto:")):
                continue
            if href.startswith("http"):
                links.append(href)
            else:
                from urllib.parse import urljoin

                links.append(urljoin(base_url, href))
    except Exception:
        return []
    return links[:200]


def extract_text(html: str, limit: int = 4000) -> str:
    """Extract readable text from HTML for summarisation."""
    try:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text[:limit]
    except Exception:
        return ""


def robots_allows(url: str) -> bool:
    """Cheap robots.txt check (best effort; failure defaults to allowed for
    public informational resources)."""
    try:
        from urllib.robotparser import RobotFileParser

        parsed = requests.utils.urlparse(url)
        rp = RobotFileParser()
        rp.set_url(f"{parsed.scheme}://{parsed.netloc}/robots.txt")
        _polite_wait(parsed.netloc)
        rp.read()
        return rp.can_fetch(USER_AGENT, url)
    except Exception:
        return True


def crawl_limited(start_url: str, max_pages: int = 8, max_depth: int = 2) -> List[Tuple[str, str]]:
    """Crawl a small number of pages from a start URL.

    Returns a list of (url, extracted_text) pairs. Bounded, polite, and
    respects robots.txt. Used only for lightweight research.
    """
    visited: List[str] = []
    results: List[Tuple[str, str]] = []
    queue: List[Tuple[str, int]] = [(start_url, 0)]

    while queue and len(results) < max_pages:
        url, depth = queue.pop(0)
        if url in visited or depth > max_depth:
            continue
        visited.append(url)
        if not robots_allows(url):
            continue
        html = fetch(url)
        if not html:
            continue
        text = extract_text(html, limit=2500)
        results.append((url, text))
        if depth < max_depth:
            for link in parse_links(html, url):
                if len(visited) + len(queue) >= max_pages * 3:
                    break
                if link not in visited and not any(link == q[0] for q in queue):
                    queue.append((link, depth + 1))
    return results

# ---------------------------------------------------------------------------
# Web search (DuckDuckGo — free, no API key, fits the free-tier constraint)
# ---------------------------------------------------------------------------
def search(query: str, max_results: int = 6) -> List[Dict[str, str]]:
    """Search the web via the DuckDuckGo HTML endpoint.

    Returns a list of {"title", "url", "snippet"} dicts. The organism uses
    this to answer its own curiosity questions from LIVE sources instead of
    only the model's frozen knowledge. Polite (rate-limited), bounded, and
    resilient: any failure returns an empty list, never raises.
    """
    results: List[Dict[str, str]] = []
    try:
        _polite_wait("html.duckduckgo.com")
        response = _session.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            timeout=DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for item in soup.select("div.result")[: max_results * 2]:
            link = item.select_one("a.result__a")
            snippet_el = item.select_one("a.result__snippet, div.result__snippet")
            if not link:
                continue
            href = link.get("href") or ""
            # DDG wraps URLs in a redirect (//duckduckgo.com/l/?uddg=<url>).
            if "uddg=" in href:
                try:
                    from urllib.parse import parse_qs, unquote, urlparse

                    qs = parse_qs(urlparse(href).query)
                    href = unquote(qs.get("uddg", [href])[0])
                except Exception:
                    pass
            title = link.get_text(strip=True)
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""
            if title and href.startswith("http"):
                results.append({"title": title, "url": href, "snippet": snippet[:300]})
            if len(results) >= max_results:
                break
    except Exception as exc:
        LOGGER.warning("Web search failed for '%s': %s", query[:80], exc)
    return results


def research(query: str, max_sources: int = 3, text_limit: int = 1800) -> str:
    """Search + read: gather live source material for a curiosity question.

    Returns a digest string combining snippets and page extracts, ready to
    be fed to the model alongside the question. Empty string when the web
    yields nothing (the model then answers from its own knowledge and the
    organism records that the answer was not live-verified).
    """
    hits = search(query, max_results=max_sources * 2)
    if not hits:
        return ""
    parts: List[str] = []
    read = 0
    for hit in hits:
        entry = f"SOURCE: {hit['title']} ({hit['url']})"
        if hit.get("snippet"):
            entry += f"\nSNIPPET: {hit['snippet']}"
        if read < max_sources and robots_allows(hit["url"]):
            html = fetch(hit["url"])
            if html:
                text = extract_text(html, limit=text_limit)
                if text:
                    entry += f"\nEXTRACT: {text[:text_limit]}"
                    read += 1
        parts.append(entry)
        if len(parts) >= max_sources * 2:
            break
    return "\n\n".join(parts)
