import { useEffect, useRef } from "react";
import { api } from "../api";
import type { RunEvent } from "../types";

type UseRunStreamOptions = {
  runId: string;
  enabled: boolean;
  afterSeq: number;
  onEvent?: (event: RunEvent) => void;
  onTerminal?: (event: RunEvent) => void;
  onError?: (message: string) => void;
};

export function useRunStream({ runId, enabled, afterSeq, onEvent, onTerminal, onError }: UseRunStreamOptions) {
  const onEventRef = useRef(onEvent);
  const onTerminalRef = useRef(onTerminal);
  const onErrorRef = useRef(onError);
  const afterSeqRef = useRef(afterSeq);

  useEffect(() => {
    onEventRef.current = onEvent;
  }, [onEvent]);

  useEffect(() => {
    onTerminalRef.current = onTerminal;
  }, [onTerminal]);

  useEffect(() => {
    onErrorRef.current = onError;
  }, [onError]);

  useEffect(() => {
    afterSeqRef.current = afterSeq;
  }, [afterSeq]);

  useEffect(() => {
    if (!runId || !enabled) {
      return;
    }

    let cancelled = false;
    let controller: AbortController | null = null;
    let reconnectTimer: number | null = null;

    async function attach() {
      while (!cancelled) {
        let terminalEvent: RunEvent | null = null;
        controller = new AbortController();
        try {
          await api.streamRun(
            runId,
            {
              onRunEvent: (event) => {
                onEventRef.current?.(event);
                if (event.type === "done" || event.type === "error" || event.type === "cancelled") {
                  terminalEvent = event;
                }
              },
              onError: (message) => {
                onErrorRef.current?.(message);
              },
            },
            {
              afterSeq: afterSeqRef.current,
              signal: controller.signal,
            },
          );
        } catch (err) {
          if ((err as Error).name === "AbortError" || cancelled) {
            return;
          }
        }

        if (cancelled) {
          return;
        }

        if (terminalEvent) {
          onTerminalRef.current?.(terminalEvent);
          return;
        }

        await new Promise<void>((resolve) => {
          reconnectTimer = window.setTimeout(resolve, 1000);
        });
      }
    }

    void attach();

    return () => {
      cancelled = true;
      if (reconnectTimer !== null) {
        window.clearTimeout(reconnectTimer);
      }
      controller?.abort();
    };
  }, [enabled, runId]);
}
