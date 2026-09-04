from metaclass.modules.paper_workflow.paper_evidence import PaperEvidenceRetriever
from metaclass.modules.paper_workflow.schemas import PaperSourceBundle, SourceReference


def _source() -> PaperSourceBundle:
    return PaperSourceBundle.model_validate(
        {
            "material_id": "mat_paper",
            "file_hash": "sha256:" + "a" * 64,
            "page_count": 2,
            "pdf_path": "paper.pdf",
            "paper_source_path": "paper_source.json",
            "paper_content_path": "paper_content.md",
            "asset_directory": "existing_assets",
            "blocks": [
                {
                    "id": "block_001_0001",
                    "type": "title",
                    "page_no": 1,
                    "section_path": ["Results"],
                    "text": "Results",
                },
                {
                    "id": "block_001_0002",
                    "type": "paragraph",
                    "page_no": 1,
                    "section_path": ["Results"],
                    "text": "We compare against three baselines under the same setting.",
                },
                {
                    "id": "block_001_0003",
                    "type": "paragraph",
                    "page_no": 1,
                    "section_path": ["Results"],
                    "text": "The proposed method improves accuracy by five points.",
                },
                {
                    "id": "block_002_0001",
                    "type": "paragraph",
                    "page_no": 2,
                    "section_path": ["Limitations"],
                    "text": "The study evaluates only one benchmark.",
                },
            ],
            "assets": [
                {
                    "id": "asset_figure_001_001",
                    "type": "figure",
                    "page_no": 1,
                    "path": "existing_assets/figure.png",
                    "caption": "Figure 1. Accuracy comparison.",
                }
            ],
        }
    )


def test_retriever_resolves_exact_block_with_adjacent_context() -> None:
    contexts = PaperEvidenceRetriever(_source()).retrieve(
        [SourceReference(page_no=1, block_id="block_001_0003")]
    )

    assert len(contexts) == 1
    assert contexts[0].exact_text == "The proposed method improves accuracy by five points."
    assert contexts[0].before_text == ("We compare against three baselines under the same setting.")
    assert contexts[0].section_path == ["Results"]


def test_retriever_uses_page_fallback_and_asset_caption() -> None:
    retriever = PaperEvidenceRetriever(_source())
    page_contexts = retriever.retrieve([SourceReference(page_no=2)])
    asset_contexts = retriever.retrieve(
        [SourceReference(page_no=1, asset_id="asset_figure_001_001")]
    )

    assert page_contexts[0].block_id == "block_002_0001"
    assert asset_contexts[0].caption == "Figure 1. Accuracy comparison."
    assert asset_contexts[0].asset_id == "asset_figure_001_001"
