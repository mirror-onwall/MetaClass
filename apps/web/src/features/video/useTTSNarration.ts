import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../shared/api";
import type { TTSArtifact } from "../../shared/types";

export type NarrationCue = {
  id: string;
  text: string;
  scope: string;
  refId?: string;
  voice?: string;
  speaker: string;
  agentId?: string;
  role?: "teacher" | "student" | "assistant" | "evaluator";
};

export type NarrationResult = "ended" | "failed" | "blocked" | "cancelled";
export type NarrationStatus = "idle" | "loading" | "playing" | "paused" | "blocked" | "error";

type PreparedNarration = {
  artifact: TTSArtifact;
  audioBuffer: AudioBuffer;
};

function narrationCacheKey(cue: NarrationCue) {
  return [cue.scope, cue.refId, cue.voice, cue.text.trim()].join("::");
}

export function useTTSNarration() {
  const contextRef = useRef<AudioContext | null>(null);
  const sourceRef = useRef<AudioBufferSourceNode | null>(null);
  const progressTimerRef = useRef<number | null>(null);
  const artifactCacheRef = useRef(new Map<string, TTSArtifact>());
  const audioCacheRef = useRef(new Map<string, AudioBuffer>());
  const pendingRef = useRef(new Map<string, Promise<PreparedNarration>>());
  const unavailableReasonRef = useRef<string | null>(null);
  const requestVersionRef = useRef(0);
  const settleRef = useRef<((result: NarrationResult) => void) | null>(null);
  const pauseRequestedRef = useRef(false);
  const [cue, setCue] = useState<NarrationCue | null>(null);
  const [status, setStatus] = useState<NarrationStatus>("idle");
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const getContext = useCallback(() => {
    let context = contextRef.current;
    if (!context || context.state === "closed") {
      context = new AudioContext();
      contextRef.current = context;
    }
    return context;
  }, []);

  const stopProgressTimer = useCallback(() => {
    if (progressTimerRef.current !== null) {
      window.clearInterval(progressTimerRef.current);
      progressTimerRef.current = null;
    }
  }, []);

  const unlock = useCallback(() => {
    const context = getContext();
    void context.resume().then(() => {
      const source = context.createBufferSource();
      source.buffer = context.createBuffer(1, 1, context.sampleRate);
      source.connect(context.destination);
      source.start();
    }).catch(() => undefined);
  }, [getContext]);

  const cancelCurrent = useCallback((clearCue = true) => {
    pauseRequestedRef.current = false;
    requestVersionRef.current += 1;
    const source = sourceRef.current;
    if (source) {
      source.onended = null;
      try {
        source.stop();
      } catch {
        // The source may already have ended.
      }
      source.disconnect();
    }
    sourceRef.current = null;
    stopProgressTimer();
    settleRef.current?.("cancelled");
    settleRef.current = null;
    setProgress(0);
    setStatus("idle");
    setError(null);
    if (clearCue) setCue(null);
  }, [stopProgressTimer]);

  const prepare = useCallback(async (nextCue: NarrationCue): Promise<PreparedNarration> => {
    if (unavailableReasonRef.current) throw new Error(unavailableReasonRef.current);
    const text = nextCue.text.trim();
    if (!text) throw new Error("语音文本为空");
    const cacheKey = narrationCacheKey(nextCue);
    const cachedArtifact = artifactCacheRef.current.get(cacheKey);
    const cachedAudio = audioCacheRef.current.get(cacheKey);
    if (cachedArtifact && cachedAudio) {
      return { artifact: cachedArtifact, audioBuffer: cachedAudio };
    }

    const pending = pendingRef.current.get(cacheKey);
    if (pending) return pending;

    const task = (async () => {
      let artifact = artifactCacheRef.current.get(cacheKey);
      artifact ??= await api.createTTSArtifact({
        text,
        scope: nextCue.scope,
        ref_id: nextCue.refId,
        voice: nextCue.voice,
      });
      artifactCacheRef.current.set(cacheKey, artifact);

      let audioBuffer = audioCacheRef.current.get(cacheKey);
      if (!audioBuffer) {
        const response = await fetch(api.ttsAudio(artifact.audio_url));
        if (!response.ok) throw new Error(`音频请求失败（${response.status}）`);
        const encoded = await response.arrayBuffer();
        audioBuffer = await getContext().decodeAudioData(encoded.slice(0));
        audioCacheRef.current.set(cacheKey, audioBuffer);
      }
      return { artifact, audioBuffer };
    })().finally(() => {
      pendingRef.current.delete(cacheKey);
    });
    pendingRef.current.set(cacheKey, task);
    return task;
  }, [getContext]);

  const prepareAll = useCallback(async (cues: NarrationCue[]) => {
    let nextIndex = 0;
    const worker = async () => {
      while (nextIndex < cues.length) {
        const cueIndex = nextIndex;
        nextIndex += 1;
        try {
          await prepare(cues[cueIndex]);
        } catch {
          // Playback reports the useful error if this cue is actually reached.
        }
      }
    };
    // Real TTS providers commonly rate-limit bursts. Sequential warming keeps
    // playback reliable and the on-demand path still retries the reached cue.
    const workerCount = Math.min(1, cues.length);
    await Promise.all(Array.from({ length: workerCount }, worker));
  }, [prepare]);

  const play = useCallback(async (nextCue: NarrationCue): Promise<NarrationResult> => {
    cancelCurrent(false);
    const requestVersion = requestVersionRef.current;
    const text = nextCue.text.trim();
    if (!text) return "ended";

    setCue(nextCue);
    setStatus("loading");
    setProgress(0);
    setError(null);

    let audioBuffer: AudioBuffer;
    try {
      ({ audioBuffer } = await prepare(nextCue));
    } catch (caught) {
      if (requestVersion !== requestVersionRef.current) return "cancelled";
      const message = caught instanceof Error ? caught.message : "语音生成失败";
      if (/余额不足|quota|insufficient|payment|402|405/i.test(message)) {
        unavailableReasonRef.current = message;
      }
      setStatus("error");
      setError(message);
      return "failed";
    }

    const context = getContext();
    try {
      await context.resume();
    } catch (caught) {
      if (requestVersion !== requestVersionRef.current) return "cancelled";
      setStatus("error");
      setError(caught instanceof Error ? caught.message : "音频加载或解码失败");
      return "failed";
    }

    if (requestVersion !== requestVersionRef.current) return "cancelled";
    if (context.state !== "running") {
      setStatus("blocked");
      setError("浏览器尚未启用声音，请点击开始自动课堂重试");
      return "blocked";
    }

    const source = context.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(context.destination);
    sourceRef.current = source;

    return new Promise<NarrationResult>((resolve) => {
      let settled = false;
      const settle = (result: NarrationResult) => {
        if (settled) return;
        settled = true;
        settleRef.current = null;
        stopProgressTimer();
        if (sourceRef.current === source) sourceRef.current = null;
        source.disconnect();
        if (result === "ended") {
          setProgress(1);
          setStatus("idle");
        }
        resolve(result);
      };
      settleRef.current = settle;
      const startedAt = context.currentTime;
      progressTimerRef.current = window.setInterval(() => {
        setProgress(Math.min((context.currentTime - startedAt) / audioBuffer.duration, 1));
      }, 100);
      source.onended = () => settle("ended");
      source.start();
      if (pauseRequestedRef.current) {
        void context.suspend().then(() => setStatus("paused"));
      } else {
        setStatus("playing");
      }
    });
  }, [cancelCurrent, getContext, prepare, stopProgressTimer]);

  const pause = useCallback(() => {
    pauseRequestedRef.current = true;
    const context = contextRef.current;
    if (!sourceRef.current || !context || context.state !== "running") {
      setStatus((current) => current === "loading" ? "paused" : current);
      return;
    }
    void context.suspend().then(() => setStatus("paused"));
  }, []);

  const resume = useCallback(async () => {
    pauseRequestedRef.current = false;
    const context = contextRef.current;
    if (!sourceRef.current) return true;
    if (!context || context.state === "running") return true;
    try {
      await context.resume();
      if (String(context.state) !== "running") return false;
      setStatus("playing");
      setError(null);
      return true;
    } catch {
      setStatus("blocked");
      setError("浏览器阻止了自动播放，请再次点击继续");
      return false;
    }
  }, []);

  const clearCue = useCallback(() => {
    setCue(null);
    setProgress(0);
  }, []);

  useEffect(() => () => {
    cancelCurrent();
    void contextRef.current?.close();
  }, [cancelCurrent]);

  return {
    cue,
    status,
    progress,
    error,
    prepareAll,
    play,
    pause,
    resume,
    clearCue,
    unlock,
    stop: cancelCurrent,
  };
}
