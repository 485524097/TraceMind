from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from uuid import UUID, uuid4

import httpx

from app.core.config import Settings
from evals.retrieval.diagnostics import DEFAULT_CASE_IDS, diagnose, validate_local_url
from evals.retrieval.validate_dataset import load_cases


class TemporaryCorpus:
    def __init__(self, client: httpx.Client, base_url: str, corpus: Path) -> None:
        self.client = client
        self.api = f"{base_url}/api/v1"
        self.corpus = corpus
        self.knowledge_base_id: str | None = None
        self.document_id: str | None = None
        self.version_id: str | None = None

    def provision(self, timeout: float) -> str:
        knowledge_base = self.client.post(
            f"{self.api}/knowledge-bases",
            json={
                "name": f"hybrid-generation-diagnostic-{uuid4().hex}",
                "description": "temporary generation reproducibility experiment",
            },
        )
        knowledge_base.raise_for_status()
        self.knowledge_base_id = str(knowledge_base.json()["id"])
        with self.corpus.open("rb") as source:
            upload = self.client.post(
                f"{self.api}/knowledge-bases/{self.knowledge_base_id}/documents",
                files={"file": (self.corpus.name, source, "text/markdown")},
            )
        upload.raise_for_status()
        document = upload.json()["document"]
        self.document_id = str(document["id"])
        self.version_id = str(document["latest_version"]["id"])
        return self.wait_for_generation(previous=None, timeout=timeout)

    def force_reindex(self, previous: str, timeout: float) -> str:
        response = self.client.post(
            f"{self.api}/knowledge-bases/{self.knowledge_base_id}/documents/"
            f"{self.document_id}/versions/{self.version_id}/index",
            json={"force": True},
        )
        response.raise_for_status()
        return self.wait_for_generation(previous=previous, timeout=timeout)

    def wait_for_generation(self, *, previous: str | None, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = self.client.get(
                f"{self.api}/knowledge-bases/{self.knowledge_base_id}/documents/{self.document_id}"
            )
            response.raise_for_status()
            version = response.json()["latest_version"]
            if version["parse_status"] == "failed" or version["index_status"] == "failed":
                raise RuntimeError("temporary corpus processing failed")
            active = version.get("active_index_generation")
            if (
                version["parse_status"] == "succeeded"
                and version["index_status"] == "succeeded"
                and isinstance(active, str)
                and active != previous
            ):
                return active
            time.sleep(0.5)
        raise TimeoutError("temporary corpus indexing timed out")

    def cleanup(self) -> list[str]:
        failures: list[str] = []
        if self.knowledge_base_id is None:
            return failures
        if self.document_id is not None:
            try:
                response = self.client.delete(
                    f"{self.api}/knowledge-bases/{self.knowledge_base_id}/documents/"
                    f"{self.document_id}"
                )
                if response.status_code != 204:
                    failures.append(f"document cleanup HTTP {response.status_code}")
            except httpx.HTTPError as exc:
                failures.append(f"document cleanup {type(exc).__name__}")
        try:
            response = self.client.delete(f"{self.api}/knowledge-bases/{self.knowledge_base_id}")
            if response.status_code != 204:
                failures.append(f"knowledge base cleanup HTTP {response.status_code}")
        except httpx.HTTPError as exc:
            failures.append(f"knowledge base cleanup {type(exc).__name__}")
        return failures


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run three Hybrid index generations")
    parser.add_argument("--base-url", type=validate_local_url, required=True)
    parser.add_argument("--qdrant-url", type=validate_local_url, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--index-timeout", type=float, default=1_200)
    parser.add_argument("--request-timeout", type=float, default=120)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    selected = set(DEFAULT_CASE_IDS)
    cases = [case for case in load_cases(args.dataset) if case.id in selected]
    settings = Settings()
    reports: list[dict[str, object]] = []
    cleanup_failures: list[str] = []
    temporary: TemporaryCorpus | None = None
    with httpx.Client(timeout=args.request_timeout, trust_env=False) as client:
        temporary = TemporaryCorpus(client, args.base_url, args.corpus)
        try:
            generation = temporary.provision(args.index_timeout)
            if temporary.knowledge_base_id is None or temporary.document_id is None:
                raise RuntimeError("temporary corpus identifiers are unavailable")
            for index in range(1, 4):
                report = diagnose(
                    settings=settings,
                    qdrant_url=args.qdrant_url,
                    knowledge_base_id=UUID(temporary.knowledge_base_id),
                    document_id=UUID(temporary.document_id),
                    generation=UUID(generation),
                    cases=cases,
                    repetitions=5 if index == 1 else 1,
                )
                reports.append(report)
                args.output.mkdir(parents=True, exist_ok=True)
                (args.output / f"generation-{index}.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                if index < 3:
                    generation = temporary.force_reindex(generation, args.index_timeout)
        finally:
            cleanup_failures = temporary.cleanup()
    summary = {
        "generations": [report["generation"] for report in reports],
        "cleanup_succeeded": not cleanup_failures,
        "cleanup_failures": cleanup_failures,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0 if len(reports) == 3 and not cleanup_failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
