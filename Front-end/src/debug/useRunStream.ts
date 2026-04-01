import { useEffect, useRef, useState } from "react";

import { api } from "../api";
import type { RunEvent } from "../types";
import { getHighestSeq, isTerminalRunEvent, mergeRunEvents } from "./debugUtils";

const RECONNECT_DELAY_MS = 750;
const BACKFILL_BATCH_SIZE = 200;

type UseRunStreamOptions = {
  runId: string;
  enabled: boolean;
  afterSeq: number;
  onEvent: (event: RunEvent) => void;
  onTerminal?: (event: RunEvent) => void;
};

function delay(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

export function useRunStream({ runId, enabled, afterSeq, onEvent, onTerminal }: UseRunStreamOptions) {
  const [error, setError] = useState("");
  const lastSeenSeqRef = useRef(afterSeq);
  const onEventRef = useRef(onEvent);
  const onTerminalRef = useRef(onTerminal);

  useEffect(() => {
    lastSeenSeqRef.current = afterSeq;
  }, [afterSeq]);

  useEffect(() => {
    onEventRef.current = onEvent;
  }, [onEvent]);

  useEffect(() => {
    onTerminalRef.current = onTerminal;
  }, [onTerminal]);

  useEffect(() => {
    if (!runId || !enabled) {
      setError("");
      return;
    }

    let cancelled = false;
    let terminalSeen = false;
    let controller: AbortController | null = null;

    async function backfillGap() {
      let merged: RunEvent[] = [];
      while (!cancelled) {
        const batch = await api.listRunEvents(runId, {
          afterSeq: lastSeenSeqRef.current,
          limit: BACKFILL_BATCH_SIZE,
        });
        if (batch.length === 0) {
          break;
        }
        merged = mergeRunEvents(merged, batch);
        lastSeenSeqRef.current = Math.max(lastSeenSeqRef.current, getHighestSeq(batch));
        for (const event of batch) {
          onEventRef.current(event);
          if (isTerminalRunEvent(event)) {
            terminalSeen = true;
            onTerminalRef.current?.(event);
          }
        }
        if (batch.length < BACKFILL_BATCH_SIZE) {
          break;
        }
      }
    }

    async function connect() {
      while (!cancelled && !terminalSeen) {
        controller = new AbortController();
        try {
          await api.streamRun(
            runId,
            {
              onRunEvent: (event) => {
                lastSeenSeqRef.current = Math.max(lastSeenSeqRef.current, Number(event.seq || 0));
                onEventRef.current(event);
                if (isTerminalRunEvent(event)) {
                  terminalSeen = true;
                  onTerminalRef.current?.(event);
                }
              },
            },
            {
              afterSeq: lastSeenSeqRef.current,
              signal: controller.signal,
            },
          );
          if (cancelled || terminalSeen) {
            break;
          }
          await backfillGap();
          if (cancelled || terminalSeen) {
            break;
          }
          await delay(RECONNECT_DELAY_MS);
        } catch (nextError) {
          if (cancelled || (nextError as Error).name === "AbortError") {
            break;
          }
          setError((nextError as Error).message);
          await backfillGap();
          if (cancelled || terminalSeen) {
            break;
          }
          await delay(RECONNECT_DELAY_MS);
        }
      }
    }

    setError("");
    void connect();

    return () => {
      cancelled = true;
      controller?.abort();
    };
  }, [enabled, runId]);

  return { error };
}
