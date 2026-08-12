import re

_CITATION = re.compile(r"\[S(\d+)\]")
_INCOMPLETE = re.compile(r"\[S\d*$")


class StreamingCitationGuard:
    def __init__(self, allowed_source_ids: set[str]) -> None:
        self.allowed_source_ids = allowed_source_ids
        self.valid_citation_count = 0
        self.invalid_citation_count = 0
        self._tail = ""

    @property
    def grounded(self) -> bool:
        return self.valid_citation_count > 0

    def push(self, text: str) -> str:
        combined = self._tail + text
        self._tail = ""
        last_bracket = combined.rfind("[")
        if last_bracket >= 0 and _INCOMPLETE.fullmatch(combined[last_bracket:]):
            self._tail = combined[last_bracket:]
            combined = combined[:last_bracket]
        return _CITATION.sub(self._replace, combined)

    def finish(self) -> str:
        tail, self._tail = self._tail, ""
        return _CITATION.sub(self._replace, tail)

    def _replace(self, match: re.Match[str]) -> str:
        citation = f"S{match.group(1)}"
        if citation in self.allowed_source_ids:
            self.valid_citation_count += 1
            return match.group(0)
        self.invalid_citation_count += 1
        return ""
