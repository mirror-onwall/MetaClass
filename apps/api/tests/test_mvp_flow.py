from collections.abc import Iterator
from io import BytesIO
from pathlib import Path

import fitz
import pytest
from fastapi.testclient import TestClient
from pptx import Presentation

from metaclass.main import create_app


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(tmp_path)) as test_client:
        yield test_client


def make_pdf() -> bytes:
    document = fitz.open()
    page = document.new_page(width=800, height=450)
    page.insert_text((72, 90), "Matrix Multiplication", fontsize=28)
    page.insert_text((72, 140), "Rows are multiplied by columns.", fontsize=16)
    payload = document.tobytes()
    document.close()
    return payload


def make_pptx() -> bytes:
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = "Classroom Plan"
    slide.placeholders[1].text = "A plan contains typed teaching actions."
    stream = BytesIO()
    presentation.save(stream)
    return stream.getvalue()


def test_pptx_upload_and_parse(client: TestClient) -> None:
    upload = client.post(
        "/api/v1/materials",
        files={
            "file": (
                "lesson.pptx",
                make_pptx(),
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            )
        },
    )
    parsed = client.post(f"/api/v1/materials/{upload.json()['id']}/parse")
    assert parsed.status_code == 200, parsed.text
    assert parsed.json()[0]["title"] == "Classroom Plan"
    assert Path(parsed.json()[0]["image_path"]).exists()


def test_combined_upload_and_parse(client: TestClient) -> None:
    response = client.post(
        "/api/v1/materials/process",
        files={"file": ("lesson.pdf", make_pdf(), "application/pdf")},
    )
    assert response.status_code == 201
    assert response.json()["material"]["status"] == "parsed"
    assert response.json()["pages"][0]["source_refs"][0]["page_no"] == 1


def test_complete_mvp_flow(client: TestClient) -> None:

    upload = client.post(
        "/api/v1/materials",
        files={"file": ("lesson.pdf", make_pdf(), "application/pdf")},
    )
    assert upload.status_code == 201
    material_id = upload.json()["id"]

    parsed = client.post(f"/api/v1/materials/{material_id}/parse")
    assert parsed.status_code == 200
    assert parsed.json()[0]["source_refs"][0]["page_no"] == 1
    assert "summary" not in parsed.json()[0]
    assert "knowledge_points" not in parsed.json()[0]
    assert Path(parsed.json()[0]["image_path"]).exists()

    content = client.post(f"/api/v1/materials/{material_id}/learning-content")
    assert content.status_code == 201
    content_id = content.json()["id"]
    assert content.json()["sections"][0]["source_refs"]
    understandings = client.get(f"/api/v1/materials/{material_id}/understandings")
    assert understandings.status_code == 200
    assert understandings.json()[0]["provider"] == "fake"
    assert understandings.json()[0]["summary"]

    plan = client.post(f"/api/v1/learning-contents/{content_id}/classroom-plans")
    assert plan.status_code == 201
    assert [item["type"] for item in plan.json()["scenes"][0]["actions"]] == [
        "SHOW_PAGE",
        "EXPLAIN",
        "ASK_QUIZ",
        "GIVE_FEEDBACK",
        "END",
    ]

    session = client.post(f"/api/v1/classroom-plans/{plan.json()['id']}/sessions")
    session_id = session.json()["id"]
    assert (
        client.post(f"/api/v1/classroom-sessions/{session_id}/next").json()["action"]["type"]
        == "SHOW_PAGE"
    )
    assert (
        client.post(f"/api/v1/classroom-sessions/{session_id}/next").json()["action"]["type"]
        == "EXPLAIN"
    )
    quiz = client.post(f"/api/v1/classroom-sessions/{session_id}/next").json()
    assert quiz["action"]["type"] == "ASK_QUIZ"

    answer = client.post(
        f"/api/v1/classroom-sessions/{session_id}/answers", json={"selected_index": 0}
    )
    assert answer.json()["correct"] is True
    assert answer.json()["session"]["mastery"][0]["value"] == 1.0

    question = client.post(
        f"/api/v1/classroom-sessions/{session_id}/questions",
        json={"question": "How does it work?"},
    )
    assert question.json()["source_refs"][0]["page_no"] == 1

    job = client.post(f"/api/v1/learning-contents/{content_id}/videos")
    assert job.status_code == 201
    assert job.json()["status"] == "finished", job.json().get("error")
    assert job.json()["progress"] == 1.0
    result = client.get(f"/api/v1/video-jobs/{job.json()['id']}/result")
    assert result.status_code == 200
    assert result.json()["job_id"] == job.json()["id"]
    assert Path(result.json()["video_path"]).stat().st_size > 0
    assert Path(result.json()["subtitles_path"]).read_text(encoding="utf-8")
    assert result.json()["duration_seconds"] > 0


def test_rejects_unsupported_file_type(client: TestClient) -> None:
    response = client.post(
        "/api/v1/materials",
        files={"file": ("notes.txt", b"plain text", "text/plain")},
    )
    assert response.status_code == 415


def test_cannot_build_content_before_parse(client: TestClient) -> None:
    upload = client.post(
        "/api/v1/materials",
        files={"file": ("lesson.pdf", make_pdf(), "application/pdf")},
    )

    response = client.post(f"/api/v1/materials/{upload.json()['id']}/learning-content")
    assert response.status_code == 409


def test_repeated_parse_replaces_pages(client: TestClient) -> None:
    upload = client.post(
        "/api/v1/materials",
        files={"file": ("lesson.pdf", make_pdf(), "application/pdf")},
    )
    material_id = upload.json()["id"]

    first = client.post(f"/api/v1/materials/{material_id}/parse")
    second = client.post(f"/api/v1/materials/{material_id}/parse")
    listed = client.get(f"/api/v1/materials/{material_id}/pages")

    assert first.status_code == second.status_code == listed.status_code == 200
    assert len(first.json()) == len(second.json()) == len(listed.json()) == 1
    assert first.json()[0]["id"] == second.json()[0]["id"]


def create_classroom_session(client: TestClient) -> str:
    processed = client.post(
        "/api/v1/materials/process",
        files={"file": ("lesson.pdf", make_pdf(), "application/pdf")},
    )
    material_id = processed.json()["material"]["id"]
    content = client.post(f"/api/v1/materials/{material_id}/learning-content")
    plan = client.post(f"/api/v1/learning-contents/{content.json()['id']}/classroom-plans")
    session = client.post(f"/api/v1/classroom-plans/{plan.json()['id']}/sessions")
    return session.json()["id"]


def test_cannot_answer_before_quiz(client: TestClient) -> None:
    session_id = create_classroom_session(client)

    response = client.post(
        f"/api/v1/classroom-sessions/{session_id}/answers",
        json={"selected_index": 0},
    )
    assert response.status_code == 409


def test_rejects_answer_index_outside_quiz_options(client: TestClient) -> None:
    session_id = create_classroom_session(client)
    for _ in range(3):
        client.post(f"/api/v1/classroom-sessions/{session_id}/next")

    response = client.post(
        f"/api/v1/classroom-sessions/{session_id}/answers",
        json={"selected_index": 999},
    )
    assert response.status_code == 422
