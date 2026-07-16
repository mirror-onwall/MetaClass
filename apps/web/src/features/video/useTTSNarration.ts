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

export function useTTSNarration() {
  const contextRef = useRef<AudioContext | null>(null);
  const sourceRef = useRef<AudioBufferSourceNode | null>(null);
  const progressTimerRef = useRef<number | null>(null);
  const cacheRef = useRef(new Map<string, TTSArtifact>());
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

  const play = useCallback(async (nextCue: NarrationCue): Promise<NarrationResult> => {
    cancelCurrent(false);
    const requestVersion = requestVersionRef.current;
    const text = nextCue.text.trim();
    if (!text) return "ended";

    setCue(nextCue);
    setStatus("loading");
    setProgress(0);
    setError(null);
    const cacheKey = [nextCue.scope, nextCue.refId, nextCue.voice, text].join("::");

    let artifact = cacheRef.current.get(cacheKey);
    try {
      artifact ??= await api.createTTSArtifact({
        text,
        scope: nextCue.scope,
        ref_id: nextCue.refId,
        voice: nextCue.voice,
      });
      cacheRef.current.set(cacheKey, artifact);
    } catch (caught) {
      if (requestVersion !== requestVersionRef.current) return "cancelled";
      setStatus("error");
      setError(caught instanceof Error ? caught.message : "语音生成失败");
      return "failed";
    }

    let audioBuffer: AudioBuffer;
    const context = getContext();
    try {
      const response = await fetch(api.ttsAudio(artifact.audio_url));
      if (!response.ok) throw new Error(`音频请求失败（${response.status}）`);
      const encoded = await response.arrayBuffer();
      audioBuffer = await context.decodeAudioData(encoded.slice(0));
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
          setCue(null);
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
  }, [cancelCurrent, getContext, stopProgressTimer]);

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
    if (!sourceRef.current || !context || context.state === "running") return true;
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

  useEffect(() => () => {
    cancelCurrent();
    void contextRef.current?.close();
  }, [cancelCurrent]);

  return {
    cue,
    status,
    progress,
    error,
    play,
    pause,
    resume,
    unlock,
    stop: cancelCurrent,
  };
}
