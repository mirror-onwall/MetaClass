from pathlib import Path
from unittest.mock import Mock

from metaclass.modules.content.schemas import LearningContent, LearningSection
from metaclass.modules.materials.schemas import SourceRef
from metaclass.modules.video.service import VideoService


def test_video_generation_failure_is_persisted_as_failed_job(tmp_path: Path) -> None:
    source_ref = SourceRef(
        material_id="mat_001",
        page_id="page_001",
        page_no=1,
        image_path=str(tmp_path / "page.png"),
    )
    content = LearningContent(
        id="content_001",
        material_id="mat_001",
        title="测试内容",
        sections=[
            LearningSection(
                id="section_001",
                title="第一节",
                summary="测试视频生成失败状态。",
                source_refs=[source_ref],
            )
        ],
    )
    contents = Mock()
    contents.get.return_value = content
    repository = Mock()
    tts = Mock()
    tts.synthesize.side_effect = RuntimeError("TTS unavailable")

    job = VideoService(tmp_path, repository, contents, tts).create_job(content.id)

    assert job.status == "failed"
    assert job.error == "TTS unavailable"
    assert repository.save_job.call_count == 3
    repository.save_result.assert_not_called()
