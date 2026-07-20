import hashlib
from dataclasses import dataclass

from app.parsing.base import ParsedBlock


@dataclass(frozen=True)
class ChunkDraft:
    chunk_index: int
    content: str
    content_hash: str
    char_count: int
    page_number: int | None
    start_line: int | None
    end_line: int | None
    section_title: str | None
    chunk_type: str
    language: str | None


@dataclass(frozen=True)
class _Piece:
    content: str
    start_offset: int
    end_offset: int


class DeterministicChunker:
    def __init__(self, *, max_chars: int, overlap_chars: int) -> None:
        if max_chars <= 0 or overlap_chars <= 0 or overlap_chars >= max_chars:
            raise ValueError("Chunk sizes must be positive and overlap must be smaller than max")
        self.max_chars = max_chars
        self.overlap_chars = overlap_chars

    def chunk(self, blocks: list[ParsedBlock]) -> list[ChunkDraft]:
        drafts: list[ChunkDraft] = []
        for block in blocks:
            if not block.text.strip():
                continue
            for piece in self._split(block.text):
                content = piece.content
                if not content.strip():
                    continue
                start_line = (
                    block.start_line + piece.start_offset if block.start_line is not None else None
                )
                end_line = (
                    block.start_line + piece.end_offset if block.start_line is not None else None
                )
                drafts.append(
                    ChunkDraft(
                        chunk_index=len(drafts),
                        content=content,
                        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                        char_count=len(content),
                        page_number=block.page_number,
                        start_line=start_line,
                        end_line=end_line,
                        section_title=block.section_title,
                        chunk_type=block.block_type,
                        language=block.language,
                    )
                )
        return drafts

    def _split(self, text: str) -> list[_Piece]:
        if len(text) <= self.max_chars:
            return [_Piece(text, 0, text.count("\n"))]
        lines = text.split("\n")
        pieces: list[_Piece] = []
        current: list[tuple[int, str]] = []
        for offset, line in enumerate(lines):
            if len(line) > self.max_chars:
                if current:
                    pieces.append(self._piece(current))
                    current = []
                pieces.extend(self._split_long_line(line, offset))
                continue
            candidate = "\n".join(value for _, value in [*current, (offset, line)])
            if current and len(candidate) > self.max_chars:
                pieces.append(self._piece(current))
                overlap = self._line_overlap(current)
                while (
                    overlap
                    and len("\n".join(value for _, value in [*overlap, (offset, line)]))
                    > self.max_chars
                ):
                    overlap.pop(0)
                current = overlap
            current.append((offset, line))
        if current:
            pieces.append(self._piece(current))
        return pieces

    def _line_overlap(self, lines: list[tuple[int, str]]) -> list[tuple[int, str]]:
        selected: list[tuple[int, str]] = []
        for item in reversed(lines):
            candidate = [item, *selected]
            if len("\n".join(value for _, value in candidate)) > self.overlap_chars:
                break
            selected = candidate
        return selected

    def _split_long_line(self, line: str, offset: int) -> list[_Piece]:
        step = self.max_chars - self.overlap_chars
        return [
            _Piece(line[start : start + self.max_chars], offset, offset)
            for start in range(0, len(line), step)
            if line[start : start + self.max_chars]
        ]

    @staticmethod
    def _piece(lines: list[tuple[int, str]]) -> _Piece:
        return _Piece(
            "\n".join(value for _, value in lines),
            lines[0][0],
            lines[-1][0],
        )
