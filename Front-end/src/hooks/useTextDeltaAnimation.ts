import { useEffect, useRef } from "react";
import type { PendingTextDeltaBatch } from "../types";
import {
  TEXT_DELTA_CHARS_PER_MS,
  TEXT_DELTA_MAX_CHARS_PER_FRAME,
  TEXT_DELTA_MIN_CHARS_PER_FRAME,
} from "../utils";

type UseTextDeltaAnimationOptions = {
  onDrainChunk: (chatId: string, assistantId: number, chunk: string) => void;
  onDrainComplete: (chatId: string) => void;
  onStreamStatusUpdate: (chatId: string) => void;
};

export function useTextDeltaAnimation({
  onDrainChunk,
  onDrainComplete,
  onStreamStatusUpdate,
}: UseTextDeltaAnimationOptions) {
  const pendingTextDeltaBatchesRef = useRef<Record<string, PendingTextDeltaBatch>>({});
  const onDrainChunkRef = useRef(onDrainChunk);
  const onDrainCompleteRef = useRef(onDrainComplete);
  const onStreamStatusUpdateRef = useRef(onStreamStatusUpdate);

  useEffect(() => {
    onDrainChunkRef.current = onDrainChunk;
  }, [onDrainChunk]);

  useEffect(() => {
    onDrainCompleteRef.current = onDrainComplete;
  }, [onDrainComplete]);

  useEffect(() => {
    onStreamStatusUpdateRef.current = onStreamStatusUpdate;
  }, [onStreamStatusUpdate]);

  function clearPendingTextDeltaBatch(chatId: string) {
    const pending = pendingTextDeltaBatchesRef.current[chatId];
    if (!pending) {
      return;
    }

    if (pending.frameId !== null) {
      window.cancelAnimationFrame(pending.frameId);
    }

    delete pendingTextDeltaBatchesRef.current[chatId];
  }

  function scheduleTextDeltaDrain(chatId: string, assistantId: number) {
    const batch = pendingTextDeltaBatchesRef.current[chatId];
    if (!batch || batch.frameId !== null) {
      return;
    }

    batch.frameId = window.requestAnimationFrame((timestamp) => {
      const currentBatch = pendingTextDeltaBatchesRef.current[chatId];
      if (!currentBatch) {
        return;
      }

      currentBatch.frameId = null;
      if (!currentBatch.delta) {
        delete pendingTextDeltaBatchesRef.current[chatId];
        onDrainCompleteRef.current(chatId);
        return;
      }

      const elapsedMs =
        currentBatch.lastDrainAt === null ? 16 : Math.max(16, timestamp - currentBatch.lastDrainAt);
      currentBatch.lastDrainAt = timestamp;

      const pacedChars = Math.max(
        TEXT_DELTA_MIN_CHARS_PER_FRAME,
        Math.min(TEXT_DELTA_MAX_CHARS_PER_FRAME, Math.ceil(elapsedMs * TEXT_DELTA_CHARS_PER_MS)),
      );
      const drainChars = Math.min(currentBatch.delta.length, pacedChars);
      const bufferedDelta = currentBatch.delta.slice(0, drainChars);

      currentBatch.delta = currentBatch.delta.slice(drainChars);
      onStreamStatusUpdateRef.current(chatId);
      onDrainChunkRef.current(chatId, assistantId, bufferedDelta);

      if (currentBatch.delta) {
        scheduleTextDeltaDrain(chatId, assistantId);
        return;
      }

      delete pendingTextDeltaBatchesRef.current[chatId];
      onDrainCompleteRef.current(chatId);
    });
  }

  function queueTextDelta(chatId: string, assistantId: number, delta: string) {
    if (!delta) {
      return;
    }

    const batches = pendingTextDeltaBatchesRef.current;
    const batch = batches[chatId] || { delta: "", frameId: null, lastDrainAt: null };
    batch.delta += delta;
    batches[chatId] = batch;
    scheduleTextDeltaDrain(chatId, assistantId);
  }

  function hasPendingDelta(chatId: string) {
    return Boolean(pendingTextDeltaBatchesRef.current[chatId]?.delta);
  }

  useEffect(
    () => () => {
      Object.values(pendingTextDeltaBatchesRef.current).forEach((batch) => {
        if (batch.frameId !== null) {
          window.cancelAnimationFrame(batch.frameId);
        }
      });
      pendingTextDeltaBatchesRef.current = {};
    },
    [],
  );

  return {
    queueTextDelta,
    clearPendingTextDeltaBatch,
    hasPendingDelta,
  };
}
