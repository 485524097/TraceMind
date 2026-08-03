import logging
import re
from dataclasses import dataclass
from pathlib import Path

import tree_sitter_java as tsjava
from tree_sitter import Language, Node, Parser

from app.parsing.base import BlockType, ParseContext, ParsedBlock, ParsedDocument, read_utf8_text
from app.parsing.code import code_blocks

logger = logging.getLogger(__name__)

TYPE_NODES = frozenset(
    {
        "class_declaration",
        "interface_declaration",
        "enum_declaration",
        "record_declaration",
        "annotation_type_declaration",
    }
)
MEMBER_KINDS = {
    "method_declaration": "method",
    "constructor_declaration": "constructor",
    "compact_constructor_declaration": "constructor",
    "field_declaration": "field",
    "static_initializer": "initializer",
    "enum_constant": "enum_constant",
}
ISOLATED_CLOSERS = re.compile(r"^[\s};,]*$")

try:
    JAVA_LANGUAGE: Language | None = Language(tsjava.language())
except Exception:  # pragma: no cover - depends on the platform ABI
    JAVA_LANGUAGE = None


@dataclass(frozen=True)
class JavaBlockSpan:
    start_byte: int
    end_byte: int
    block_type: BlockType = "code"
    symbol_kind: str | None = None
    symbol_name: str | None = None
    symbol_qualified_name: str | None = None
    symbol_signature: str | None = None


class JavaTreeSitterParser:
    parser_name = "java-tree-sitter"
    parser_version = "1"
    supported_extensions = frozenset({".java"})

    def parse(self, path: Path, context: ParseContext) -> ParsedDocument:
        text = read_utf8_text(path, context)
        source = text.encode("utf-8")
        if JAVA_LANGUAGE is None:
            return self._fallback(text)
        try:
            root = Parser(JAVA_LANGUAGE).parse(source).root_node
        except Exception as exc:
            logger.warning("Java parser fallback (%s)", type(exc).__name__)
            return self._fallback(text)
        if root.start_byte != 0 or root.end_byte != len(source):
            return self._fallback(text)

        warnings: set[str] = set()
        if root.has_error:
            warnings.add("java_partial_syntax_tree")
        spans = self._symbol_spans(root, source, warnings)
        spans = self._validated_non_overlapping(spans, len(source), warnings)
        spans.extend(self._uncovered_spans(spans, source, warnings))
        spans.sort(key=lambda span: (span.start_byte, span.end_byte))
        blocks = self._to_blocks(spans, source, warnings)
        if not blocks:
            return self._fallback(text)
        return ParsedDocument(
            blocks,
            self.parser_name,
            self.parser_version,
            sorted(warnings),
        )

    @staticmethod
    def _fallback(text: str) -> ParsedDocument:
        return ParsedDocument(
            code_blocks(text, "java"),
            "code",
            "1",
            ["java_parser_fallback"],
        )

    def _symbol_spans(self, root: Node, source: bytes, warnings: set[str]) -> list[JavaBlockSpan]:
        package = self._package_name(root, source)
        consumed_javadocs: set[tuple[int, int]] = set()
        spans: list[JavaBlockSpan] = []
        for child in root.named_children:
            if child.type in TYPE_NODES:
                self._collect_type(
                    child,
                    source,
                    package,
                    (),
                    spans,
                    consumed_javadocs,
                    warnings,
                )
        return spans

    def _collect_type(
        self,
        node: Node,
        source: bytes,
        package: str,
        enclosing: tuple[str, ...],
        spans: list[JavaBlockSpan],
        consumed_javadocs: set[tuple[int, int]],
        warnings: set[str],
    ) -> None:
        name = self._node_name(node, source)
        body = node.child_by_field_name("body")
        if name is None or body is None or not self._is_open_brace(body, source):
            warnings.add("java_symbol_invalid_range")
            return
        qualified = self._qualified(package, (*enclosing, name))
        declaration_start = node.start_byte
        start = self._attached_javadoc_start(node, source, consumed_javadocs)
        signature = self._signature(source, declaration_start, body.start_byte)
        spans.append(
            JavaBlockSpan(
                start,
                body.start_byte + 1,
                symbol_kind="type",
                symbol_name=name,
                symbol_qualified_name=qualified,
                symbol_signature=signature,
            )
        )

        for child in body.named_children:
            if child.type in TYPE_NODES:
                self._collect_type(
                    child,
                    source,
                    package,
                    (*enclosing, name),
                    spans,
                    consumed_javadocs,
                    warnings,
                )
            elif child.type == "block":
                if child.has_error:
                    warnings.add("java_symbol_invalid_range")
                    continue
                spans.append(
                    self._member_span(
                        child,
                        source,
                        package,
                        (*enclosing, name),
                        "initializer",
                        "<init-block>",
                        consumed_javadocs,
                    )
                )
            elif child.type in MEMBER_KINDS:
                if child.has_error:
                    warnings.add("java_symbol_invalid_range")
                    continue
                symbol_name = self._member_name(child, source, name)
                if symbol_name is None:
                    warnings.add("java_symbol_invalid_range")
                    continue
                spans.append(
                    self._member_span(
                        child,
                        source,
                        package,
                        (*enclosing, name),
                        MEMBER_KINDS[child.type],
                        symbol_name,
                        consumed_javadocs,
                    )
                )

    def _member_span(
        self,
        node: Node,
        source: bytes,
        package: str,
        enclosing: tuple[str, ...],
        kind: str,
        name: str,
        consumed_javadocs: set[tuple[int, int]],
    ) -> JavaBlockSpan:
        start = self._attached_javadoc_start(node, source, consumed_javadocs)
        signature_end = self._signature_end(node)
        return JavaBlockSpan(
            start,
            node.end_byte,
            symbol_kind=kind,
            symbol_name=name,
            symbol_qualified_name=self._qualified(package, (*enclosing, name)),
            symbol_signature=self._signature(source, node.start_byte, signature_end),
        )

    @staticmethod
    def _signature_end(node: Node) -> int:
        body = node.child_by_field_name("body")
        if body is not None:
            return body.start_byte
        return node.end_byte

    @staticmethod
    def _package_name(root: Node, source: bytes) -> str:
        for child in root.named_children:
            if child.type == "package_declaration":
                name = child.child_by_field_name("name")
                if name is None:
                    name = next(
                        (
                            item
                            for item in child.named_children
                            if item.type in {"identifier", "scoped_identifier"}
                        ),
                        None,
                    )
                if name is not None:
                    return source[name.start_byte : name.end_byte].decode("utf-8")
        return ""

    @staticmethod
    def _node_name(node: Node, source: bytes) -> str | None:
        name = node.child_by_field_name("name")
        if name is None:
            return None
        try:
            return source[name.start_byte : name.end_byte].decode("utf-8")
        except UnicodeDecodeError:
            return None

    def _member_name(self, node: Node, source: bytes, type_name: str) -> str | None:
        if node.type == "static_initializer":
            return "<clinit>"
        if node.type in {"constructor_declaration", "compact_constructor_declaration"}:
            return type_name
        if node.type == "field_declaration":
            declarator = next(
                (child for child in node.named_children if child.type == "variable_declarator"),
                None,
            )
            return self._node_name(declarator, source) if declarator is not None else None
        return self._node_name(node, source)

    @staticmethod
    def _qualified(package: str, parts: tuple[str, ...]) -> str:
        return ".".join(part for part in (package, *parts) if part)

    @staticmethod
    def _is_open_brace(body: Node, source: bytes) -> bool:
        return (
            0 <= body.start_byte < len(source)
            and source[body.start_byte : body.start_byte + 1] == b"{"
        )

    @staticmethod
    def _signature(source: bytes, start: int, end: int) -> str | None:
        try:
            value = source[start:end].decode("utf-8").strip()
        except UnicodeDecodeError:
            return None
        return re.sub(r"\s+", " ", value) or None

    @staticmethod
    def _attached_javadoc_start(
        node: Node,
        source: bytes,
        consumed: set[tuple[int, int]],
    ) -> int:
        previous = node.prev_named_sibling
        if previous is None or previous.parent != node.parent or previous.type != "block_comment":
            return node.start_byte
        key = (previous.start_byte, previous.end_byte)
        if key in consumed:
            return node.start_byte
        try:
            comment = source[previous.start_byte : previous.end_byte].decode("utf-8")
        except UnicodeDecodeError:
            return node.start_byte
        between = source[previous.end_byte : node.start_byte]
        if not comment.lstrip().startswith("/**") or between.strip():
            return node.start_byte
        consumed.add(key)
        return previous.start_byte

    @staticmethod
    def _validated_non_overlapping(
        spans: list[JavaBlockSpan],
        source_size: int,
        warnings: set[str],
    ) -> list[JavaBlockSpan]:
        accepted: list[JavaBlockSpan] = []
        cursor = 0
        for span in sorted(spans, key=lambda item: (item.start_byte, item.end_byte)):
            if not (0 <= span.start_byte < span.end_byte <= source_size):
                warnings.add("java_symbol_invalid_range")
                continue
            if span.start_byte < cursor:
                warnings.add("java_symbol_invalid_range")
                continue
            accepted.append(span)
            cursor = span.end_byte
        return accepted

    def _uncovered_spans(
        self,
        symbol_spans: list[JavaBlockSpan],
        source: bytes,
        warnings: set[str],
    ) -> list[JavaBlockSpan]:
        uncovered: list[JavaBlockSpan] = []
        cursor = 0
        for span in symbol_spans:
            if cursor < span.start_byte:
                self._append_code_spans(cursor, span.start_byte, source, uncovered, warnings)
            cursor = max(cursor, span.end_byte)
        if cursor < len(source):
            self._append_code_spans(cursor, len(source), source, uncovered, warnings)
        return uncovered

    @staticmethod
    def _append_code_spans(
        start: int,
        end: int,
        source: bytes,
        output: list[JavaBlockSpan],
        warnings: set[str],
    ) -> None:
        try:
            text = source[start:end].decode("utf-8")
        except UnicodeDecodeError:
            warnings.add("java_symbol_decode_failed")
            return
        if not text.strip() or ISOLATED_CLOSERS.fullmatch(text):
            return
        search_from = 0
        for block in code_blocks(text, "java"):
            encoded = block.text.encode("utf-8")
            relative = source[start:end].find(encoded, search_from)
            if relative < 0:
                warnings.add("java_symbol_invalid_range")
                continue
            block_start = start + relative
            output.append(JavaBlockSpan(block_start, block_start + len(encoded)))
            search_from = relative + len(encoded)

    @staticmethod
    def _to_blocks(
        spans: list[JavaBlockSpan],
        source: bytes,
        warnings: set[str],
    ) -> list[ParsedBlock]:
        blocks: list[ParsedBlock] = []
        for span in spans:
            try:
                text = source[span.start_byte : span.end_byte].decode("utf-8")
            except UnicodeDecodeError:
                warnings.add("java_symbol_decode_failed")
                continue
            if not text.strip():
                continue
            start_line = source[: span.start_byte].count(b"\n") + 1
            end_line = source[: span.end_byte].count(b"\n") + (
                0 if source[span.end_byte - 1 : span.end_byte] == b"\n" else 1
            )
            blocks.append(
                ParsedBlock(
                    text,
                    span.block_type,
                    start_line=start_line,
                    end_line=max(start_line, end_line),
                    language="java",
                    symbol_kind=span.symbol_kind,
                    symbol_name=span.symbol_name,
                    symbol_qualified_name=span.symbol_qualified_name,
                    symbol_signature=span.symbol_signature,
                )
            )
        return blocks
