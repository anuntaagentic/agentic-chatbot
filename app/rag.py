import csv
import hashlib
import os
import pickle
from dataclasses import dataclass

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel


@dataclass
class RagMatch:
    score: float
    conversation_id: str
    issue: str
    response: str
    category: str
    status: str
    resolution_time: str


class TechSupportRAG:
    def __init__(self, csv_path, cache_path=None, require_cache=False):
        self.csv_path = csv_path
        self.cache_path = cache_path or f"{csv_path}.pkl"
        self.require_cache = require_cache
        self.rows = []
        self.vectorizer = None
        self.matrix = None
        self._csv_hash = ""
        self._load()

    def _load(self):
        if not os.path.exists(self.csv_path):
            return
        self._csv_hash = self._hash_file(self.csv_path)
        if self._load_cache():
            return
        if self.require_cache:
            return
        with open(self.csv_path, "r", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                self.rows.append(row)
        if not self.rows:
            return
        documents = []
        for row in self.rows:
            documents.append(
                " | ".join(
                    [
                        row.get("Customer_Issue", ""),
                        row.get("Tech_Response", ""),
                        row.get("Issue_Category", ""),
                        row.get("Issue_Status", ""),
                    ]
                )
            )
        self.vectorizer = TfidfVectorizer(stop_words="english")
        self.matrix = self.vectorizer.fit_transform(documents)
        self._save_cache()

    def _hash_file(self, path):
        hasher = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(8192), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def _load_cache(self):
        if not os.path.exists(self.cache_path):
            return False
        try:
            with open(self.cache_path, "rb") as handle:
                cache = pickle.load(handle)
            if cache.get("csv_hash") != self._csv_hash:
                return False
            self.rows = cache.get("rows", [])
            self.vectorizer = cache.get("vectorizer")
            self.matrix = cache.get("matrix")
            return self.vectorizer is not None and self.matrix is not None
        except Exception:
            return False

    def _save_cache(self):
        try:
            with open(self.cache_path, "wb") as handle:
                pickle.dump(
                    {
                        "csv_hash": self._csv_hash,
                        "rows": self.rows,
                        "vectorizer": self.vectorizer,
                        "matrix": self.matrix,
                    },
                    handle,
                )
        except Exception:
            pass

    def search(self, query, top_k=5, keywords=None):
        if not self.rows or not self.vectorizer or self.matrix is None:
            return []
        query_vec = self.vectorizer.transform([query])
        scores = linear_kernel(query_vec, self.matrix).flatten()
        if keywords:
            lowered_keywords = [kw.lower() for kw in keywords]
            for idx, row in enumerate(self.rows):
                haystack = " ".join(
                    [
                        row.get("Customer_Issue", ""),
                        row.get("Tech_Response", ""),
                        row.get("Issue_Category", ""),
                    ]
                ).lower()
                if any(kw in haystack for kw in lowered_keywords):
                    scores[idx] = scores[idx] + 0.15
        ranked = scores.argsort()[::-1][:top_k]
        results = []
        for idx in ranked:
            row = self.rows[idx]
            results.append(
                RagMatch(
                    score=float(scores[idx]),
                    conversation_id=row.get("Conversation_ID", ""),
                    issue=row.get("Customer_Issue", ""),
                    response=row.get("Tech_Response", ""),
                    category=row.get("Issue_Category", ""),
                    status=row.get("Issue_Status", ""),
                    resolution_time=row.get("Resolution_Time", ""),
                )
            )
        return results
