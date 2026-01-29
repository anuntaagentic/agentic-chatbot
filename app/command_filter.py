import json
import os
from fnmatch import fnmatch


class CommandFilter:
    def __init__(self, allowlist_path, denylist_path):
        self.deny_patterns = self._load_patterns(denylist_path)

    def _load_patterns(self, path):
        if not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data.get("commands", [])

    def is_allowed(self, command):
        if self._matches(self.deny_patterns, command):
            return False, "Command blocked by denylist."
        return True, ""

    def _matches(self, patterns, command):
        for pattern in patterns:
            if fnmatch(command.lower(), pattern.lower()):
                return True
        return False
