import json
import os
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
        self.api_key = os.environ.get("TAVILY_API_KEY", "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")
        self.last_query = ""
        self.last_error = ""
        self.last_count = 0
        self._headers = {"Content-Type": "application/json"}

    def search(self, query, max_results=3):
        self.last_query = query
        self.last_error = ""
        self.last_count = 0
        if not self.enabled:
            self.last_error = "web search disabled"
            return []
        if not self.api_key:
            self.last_error = "Tavily API key missing"
            return []
        try:
            results = self._tavily_search(query, max_results)
            self.last_count = len(results)
            return results
        except Exception:
            self.last_error = "web search failed"
            return []

    def _tavily_search(self, query, max_results):
        payload = {
            "api_key": self.api_key,
            "query": query,
            "search_depth": "basic",
            "max_results": max_results,
            "include_answer": False,
            "include_images": False,
            "include_raw_content": False,
        }
        request = urllib.request.Request(
            "https://api.tavily.com/search",
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=12) as response:
            data = json.loads(response.read().decode("utf-8"))
        results = []
        for item in data.get("results", []):
            results.append(
                WebResult(
                    title=item.get("title", ""),
                    snippet=item.get("content", ""),
                    url=item.get("url", ""),
                )
            )
            if len(results) >= max_results:
                break
        return results
