import { useEffect, useMemo, useState } from "react";
import { api } from "../../shared/api";
import type { LearningContent, LearningSection, PageMetadata } from "../../shared/types";

type Props = {
  content: LearningContent | null;
  materialId?: string;
  pages: PageMetadata[];
};

function sectionForPage(
  content: LearningContent | null,
  page: PageMetadata,
  index: number,
): LearningSection | undefined {
  return (
    content?.sections.find((section) =>
      section.source_refs.some((source) => source.page_no === page.page_no),
    ) ?? content?.sections[index]
  );
}

export function SlideNarrationPlayer({ content, materialId, pages }: Props) {
  const [currentIndex, setCurrentIndex] = useState(0);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [isPaused, setIsPaused] = useState(false);

  const currentPage = pages[currentIndex];
  const currentSection = currentPage
    ? sectionForPage(content, currentPage, currentIndex)
    : undefined;
  const canSpeak = typeof window !== "undefined" && "speechSynthesis" in window;

  const narrationText = useMemo(() => {
    if (!currentPage) return "";
    if (currentSection) {
      return [currentSection.title, currentSection.summary].filter(Boolean).join("。");
    }
    return currentPage.title || `第 ${currentPage.page_no} 页暂无讲稿，请先构建学习内容。`;
  }, [currentPage, currentSection]);

  useEffect(() => {
    return () => {
      window.speechSynthesis?.cancel();
    };
  }, []);

  function stopNarration() {
    window.speechSynthesis?.cancel();
    setIsSpeaking(false);
    setIsPaused(false);
  }

  function moveTo(index: number) {
    stopNarration();
    setCurrentIndex(Math.min(Math.max(index, 0), pages.length - 1));
  }

  function playNarration() {
    if (!canSpeak || !narrationText) return;
    if (isSpeaking && isPaused) {
      window.speechSynthesis.resume();
      setIsPaused(false);
      return;
    }
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(narrationText);
    utterance.lang = "zh-CN";
    utterance.rate = 0.95;
    utterance.onend = () => {
      setIsSpeaking(false);
      setIsPaused(false);
    };
    utterance.onerror = () => {
      setIsSpeaking(false);
      setIsPaused(false);
    };
    setIsSpeaking(true);
    setIsPaused(false);
    window.speechSynthesis.speak(utterance);
  }

  function pauseNarration() {
    if (!canSpeak || !isSpeaking) return;
    window.speechSynthesis.pause();
    setIsPaused(true);
  }

  if (!currentPage || !materialId) {
    return (
      <div className="slide-narrator empty">
        <p>解析完成后，这里会显示逐页讲解播放器。</p>
      </div>
    );
  }

  const progress = ((currentIndex + 1) / pages.length) * 100;

  return (
    <div className="slide-narrator">
      <div className="slide-viewer">
        <img
          src={api.pageImage(materialId, currentPage.page_no)}
          alt={`第 ${currentPage.page_no} 页`}
        />
      </div>
      <div className="narration-panel">
        <small>SLIDE NARRATION</small>
        <h1>{currentSection?.title ?? currentPage.title}</h1>
        <p>{currentSection?.summary ?? "学习内容生成后，这里会显示当前页讲稿。"}</p>
        <div className="narration-meta">
          <span>
            {currentIndex + 1} / {pages.length}
          </span>
          <i>
            <b style={{ width: `${progress}%` }} />
          </i>
        </div>
        <div className="narration-controls">
          <button onClick={() => moveTo(currentIndex - 1)} disabled={currentIndex === 0}>
            上一页
          </button>
          <button onClick={isSpeaking && !isPaused ? pauseNarration : playNarration}>
            {isSpeaking && !isPaused ? "暂停" : isPaused ? "继续" : "播放讲解"}
          </button>
          <button
            onClick={() => moveTo(currentIndex + 1)}
            disabled={currentIndex === pages.length - 1}
          >
            下一页
          </button>
        </div>
        {!canSpeak && <em>当前浏览器不支持 speechSynthesis，后续可接后端 TTS 音频。</em>}
      </div>
    </div>
  );
}
