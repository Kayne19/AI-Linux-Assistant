import { api, dispatchRunEvent, isTerminalRunEvent, type StreamHandlers } from "./api";

type StreamRunSessionOptions = {
  afterSeq?: number;
  signal?: AbortSignal;
  reconnectDelayMs?: number;
};

function waitForDelay(delayMs: number, signal?: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const timeoutId = window.setTimeout(() => {
      cleanup();
      resolve();
    }, delayMs);

    function onAbort() {
      cleanup();
      const error = new Error("Aborted");
      error.name = "AbortError";
      reject(error);
    }

    function cleanup() {
      window.clearTimeout(timeoutId);
      signal?.removeEventListener("abort", onAbort);
    }

    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export async function streamRunSession(
  runId: string,
  handlers: StreamHandlers = {},
  options: StreamRunSessionOptions = {},
): Promise<void> {
  const signal = options.signal;
  const reconnectDelayMs = Math.max(250, options.reconnectDelayMs || 1000);
  let afterSeq = Math.max(0, options.afterSeq || 0);

  while (!signal?.aborted) {
    let terminalSeen = false;
    try {
      await api.streamRun(
        runId,
        {
          ...handlers,
          onSequence: (seq) => {
            afterSeq = Math.max(afterSeq, seq);
            handlers.onSequence?.(seq);
          },
          onRunEvent: (event) => {
            afterSeq = Math.max(afterSeq, event.seq || 0);
            handlers.onRunEvent?.(event);
            if (isTerminalRunEvent(event)) {
              terminalSeen = true;
            }
          },
        },
        {
          afterSeq,
          signal,
        },
      );
      if (terminalSeen || signal?.aborted) {
        return;
      }
    } catch (err) {
      const name = (err as Error).name || "";
      if (name === "AbortError" || signal?.aborted) {
        return;
      }
    }

    const missingEvents = await api.listRunEvents(runId, { afterSeq });
    for (const event of missingEvents) {
      afterSeq = Math.max(afterSeq, event.seq || 0);
      dispatchRunEvent(event, handlers);
      if (isTerminalRunEvent(event)) {
        return;
      }
    }

    if (signal?.aborted) {
      return;
    }
    await waitForDelay(reconnectDelayMs, signal);
  }
}
