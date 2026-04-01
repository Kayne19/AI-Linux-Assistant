import { useEffect, useRef } from "react";
import { api } from "../api";
import type { RunEvent } from "../types";

type UseRunStreamOptions = {
  runId: string;
  enabled: boolean;
  initialAfterSeq: number;
  onLiveEvent: (event: RunEvent) => void;
  onBackfill: (afterSeq: number) => Promise<void>;
  onTerminal: (event: RunEvent) => void;
};

function wait(delayMs: number) {
  return new Promise((resolve) => {
    window.setTimeout(resolve, delayMs);
  });
}

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
      while (!stopped) {
        let terminalSeen = false;
        try {
          await api.streamRun(
            runId,
            {
              onSequence: (seq) => {
                lastSeenSeqRef.current = Math.max(lastSeenSeqRef.current, seq);
              },
              onRunEvent: (event) => {
                lastSeenSeqRef.current = Math.max(lastSeenSeqRef.current, event.seq || 0);
                onLiveEventRef.current(event);
                if (event.type === "done" || event.type === "error" || event.type === "cancelled") {
                  terminalSeen = true;
                  onTerminalRef.current(event);
                }
              },
            },
            {
              afterSeq: lastSeenSeqRef.current,
              signal: controller.signal,
            },
          );
          if (terminalSeen || stopped) {
            return;
          }
        } catch (err) {
          const name = (err as Error).name || "";
          if (name === "AbortError" || stopped) {
            return;
          }
        }

        await onBackfillRef.current(lastSeenSeqRef.current);
        if (stopped || terminalSeen) {
          return;
        }
        await wait(1000);
      }
    }

    void connect();

    return () => {
      stopped = true;
      controller.abort();
    };
  }, [enabled, runId]);
}
