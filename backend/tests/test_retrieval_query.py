from uuid import UUID, uuid4

from app.models.document import Document
from app.services.retrieval_query import ExplicitDocumentPathResolver

ALLOWED_EXTENSIONS = {".java", ".md"}


def document(knowledge_base_id: UUID, relative_path: str) -> Document:
    return Document(
        id=uuid4(),
        knowledge_base_id=knowledge_base_id,
        name=relative_path.rsplit("/", 1)[-1],
        normalized_name=relative_path.rsplit("/", 1)[-1].casefold(),
        relative_path=relative_path,
        normalized_path=relative_path.casefold(),
        source_type="upload",
    )


class FakeDocumentRepository:
    def __init__(self, documents: list[Document]) -> None:
        self.documents = documents

    async def get_document_by_normalized_path(
        self, knowledge_base_id: UUID, normalized_path: str
    ) -> Document | None:
        return next(
            (
                item
                for item in self.documents
                if item.knowledge_base_id == knowledge_base_id
                and item.normalized_path == normalized_path
            ),
            None,
        )


async def test_full_path_resolves_exact_document_and_semantic_query() -> None:
    knowledge_base_id = uuid4()
    main = document(knowledge_base_id, "src/main/java/demo/UserService.java")
    test = document(knowledge_base_id, "src/test/java/demo/UserService.java")
    resolver = ExplicitDocumentPathResolver(
        FakeDocumentRepository([main, test]), ALLOWED_EXTENSIONS
    )

    prepared = await resolver.prepare(
        knowledge_base_id,
        "src/main/java/demo/UserService.java 中 source 方法返回什么？",
        document_id=None,
    )

    assert prepared.path_scope_mode == "exact"
    assert prepared.explicit_relative_path == main.relative_path
    assert prepared.scoped_document_id == main.id
    assert prepared.semantic_query == "source 方法返回什么？"


async def test_backslashes_are_normalized_and_scope_stays_in_knowledge_base() -> None:
    knowledge_base_id = uuid4()
    other_knowledge_base_id = uuid4()
    local = document(knowledge_base_id, "src/test/java/demo/UserService.java")
    other = document(other_knowledge_base_id, "src/test/java/demo/UserService.java")
    resolver = ExplicitDocumentPathResolver(
        FakeDocumentRepository([local, other]), ALLOWED_EXTENSIONS
    )

    prepared = await resolver.prepare(
        knowledge_base_id,
        r"src\test\java\demo\UserService.java 里返回什么？",
        document_id=None,
    )

    assert prepared.scoped_document_id == local.id
    assert prepared.explicit_relative_path == local.relative_path
    assert prepared.semantic_query == "返回什么？"


async def test_path_with_repeated_separators_and_ascii_question_mark_is_scoped() -> None:
    knowledge_base_id = uuid4()
    main = document(knowledge_base_id, "src/main/UserService.java")
    resolver = ExplicitDocumentPathResolver(FakeDocumentRepository([main]), ALLOWED_EXTENSIONS)

    prepared = await resolver.prepare(
        knowledge_base_id,
        "./src//main/UserService.java?",
        document_id=None,
    )

    assert prepared.scoped_document_id == main.id
    assert prepared.semantic_query == "UserService.java"


async def test_path_with_spaces_is_resolved_from_surrounding_question_text() -> None:
    knowledge_base_id = uuid4()
    spaced = document(knowledge_base_id, "src/my project/User Service.java")
    resolver = ExplicitDocumentPathResolver(FakeDocumentRepository([spaced]), ALLOWED_EXTENSIONS)

    prepared = await resolver.prepare(
        knowledge_base_id,
        "请问 src/my project/User Service.java 中 source 返回什么？",
        document_id=None,
    )

    assert prepared.scoped_document_id == spaced.id
    assert prepared.explicit_relative_path == spaced.relative_path
    assert prepared.semantic_query == "请问 source 返回什么？"


async def test_basename_missing_and_invalid_paths_remain_unscoped() -> None:
    knowledge_base_id = uuid4()
    resolver = ExplicitDocumentPathResolver(FakeDocumentRepository([]), ALLOWED_EXTENSIONS)

    for query in (
        "UserService.java 返回什么？",
        "missing/UserService.java 返回什么？",
        "../src/UserService.java 返回什么？",
        r"C:\src\UserService.java 返回什么？",
    ):
        prepared = await resolver.prepare(knowledge_base_id, query, document_id=None)
        assert prepared.path_scope_mode == "none"
        assert prepared.scoped_document_id is None
        assert prepared.semantic_query == query


async def test_explicit_document_id_has_priority_over_automatic_path() -> None:
    knowledge_base_id = uuid4()
    explicit_document_id = uuid4()
    matching = document(knowledge_base_id, "src/main/UserService.java")
    repository = FakeDocumentRepository([matching])
    resolver = ExplicitDocumentPathResolver(repository, ALLOWED_EXTENSIONS)

    prepared = await resolver.prepare(
        knowledge_base_id,
        "src/main/UserService.java 返回什么？",
        document_id=explicit_document_id,
    )

    assert prepared.scoped_document_id == explicit_document_id
    assert prepared.path_scope_mode == "none"
    assert prepared.semantic_query == "src/main/UserService.java 返回什么？"
