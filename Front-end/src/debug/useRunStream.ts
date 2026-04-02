import { useEffect, useRef } from "react";
import type { RunEvent } from "../types";
import { streamRunSession } from "../runStreamSession";

type UseRunStreamOptions = {
  runId: string;
  enabled: boolean;
  initialAfterSeq: number;
  onLiveEvent: (event: RunEvent) => void;
  onBackfill: (afterSeq: number) => Promise<void>;
  onTerminal: (event: RunEvent) => void;
};

export function useRunStream({
  runId,
  enabled,
  initialAfterSeq,
  onLiveEvent,
  onBackfill,
  onTerminal,
}: UseRunStreamOptions) {
  const lastSeenSeqRef = useRef(0);
  const onLiveEventRef = useRef(onLiveEvent);
  const onBackfillRef = useRef(onBackfill);
  const onTerminalRef = useRef(onTerminal);

  useEffect(() => {
    onLiveEventRef.current = onLiveEvent;
  }, [onLiveEvent]);

  useEffect(() => {
    onBackfillRef.current = onBackfill;
  }, [onBackfill]);

  useEffect(() => {
    onTerminalRef.current = onTerminal;
  }, [onTerminal]);

  useEffect(() => {
    lastSeenSeqRef.current = initialAfterSeq;
  }, [runId, initialAfterSeq]);

  useEffect(() => {
    if (!enabled || !runId) {
      return;
    }

    let stopped = false;
    const controller = new AbortController();

    async function connect() {
      await streamRunSession(
        runId,
        {
          onSequence: (seq) => {
            lastSeenSeqRef.current = Math.max(lastSeenSeqRef.current, seq);
          },
          onRunEvent: (event) => {
            lastSeenSeqRef.current = Math.max(lastSeenSeqRef.current, event.seq || 0);
            onLiveEventRef.current(event);
            if (event.type === "done" || event.type === "error" || event.type === "cancelled" || event.type === "paused") {
              onTerminalRef.current(event);
            }
          },
        },
        {
          afterSeq: lastSeenSeqRef.current,
          signal: controller.signal,
        },
      );
    }

    void connect().catch(async (err: Error) => {
      if ((err.name || "") === "AbortError" || stopped) {
        return;
      }
      await onBackfillRef.current(lastSeenSeqRef.current);
    });

    return () => {
      stopped = true;
      controller.abort();
    };
  }, [enabled, runId]);
}
