import html
import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass


@dataclass
class WebResult:
    title: str
    snippet: str
    url: str


class WebSearch:
    def __init__(self):
        self.enabled = os.environ.get("ENABLE_WEB_SEARCH", "1") == "1"
        self.last_query = ""
        self.last_error = ""
        self.last_count = 0
        self._headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        }

    def search(self, query, max_results=3):
        self.last_query = query
        self.last_error = ""
        self.last_count = 0
        if not self.enabled:
            self.last_error = "web search disabled"
            return []
        try:
            results = self._instant_answer(query, max_results)
            if not results:
                results = self._fallback_html(query, max_results)
            self.last_count = len(results)
            return results
        except Exception:
            self.last_error = "web search failed"
            return []

    def _instant_answer(self, query, max_results):
        try:
            params = urllib.parse.urlencode(
                {"q": query, "format": "json", "no_redirect": 1, "no_html": 1}
            )
            url = f"https://api.duckduckgo.com/?{params}"
            request = urllib.request.Request(url, headers=self._headers)
            with urllib.request.urlopen(request, timeout=8) as response:
                data = json.loads(response.read().decode("utf-8"))
            results = []
            related = data.get("RelatedTopics", [])
            for item in related:
                if isinstance(item, dict) and "Text" in item and "FirstURL" in item:
                    results.append(
                        WebResult(
                            title=item.get("Text", ""),
                            snippet=item.get("Text", ""),
                            url=item.get("FirstURL", ""),
                        )
                    )
                if len(results) >= max_results:
                    break
            return results
        except Exception:
            return []

    def _fallback_html(self, query, max_results):
        try:
            params = urllib.parse.urlencode({"q": query})
            url = f"https://html.duckduckgo.com/html/?{params}"
            request = urllib.request.Request(url, headers=self._headers)
            with urllib.request.urlopen(request, timeout=8) as response:
                html_text = response.read().decode("utf-8", errors="ignore")
            results = []
            pattern = r'class="result__a" href="([^"]+)"[^>]*>(.*?)</a>'
            for match in re.finditer(pattern, html_text):
                link = html.unescape(match.group(1))
                title = html.unescape(re.sub(r"<.*?>", "", match.group(2)))
                link = self._normalize_link(link)
                if link and title:
                    results.append(WebResult(title=title, snippet=title, url=link))
                if len(results) >= max_results:
                    break
            return results
        except Exception:
            self.last_error = "html fallback failed"
            return []

    def _normalize_link(self, link):
        if not link:
            return link
        try:
            parsed = urllib.parse.urlparse(link)
            if parsed.path == "/l/":
                query = urllib.parse.parse_qs(parsed.query)
                target = query.get("uddg")
                if target:
                    return urllib.parse.unquote(target[0])
            return link
        except Exception:
            return link
