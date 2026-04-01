import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { RunEvent } from "../types";

const EVENT_PAGE_SIZE = 250;

function mergeEvents(current: RunEvent[], incoming: RunEvent[]): RunEvent[] {
  const bySeq = new Map<number, RunEvent>();
  for (const event of current) {
    bySeq.set(event.seq, event);
  }
  for (const event of incoming) {
    bySeq.set(event.seq, event);
  }
  return Array.from(bySeq.values()).sort((left, right) => left.seq - right.seq);
}

type RunEventsState = {
  events: RunEvent[];
  highestSeqSeen: number;
  loading: boolean;
  error: string;
  appendEvent: (event: RunEvent) => void;
};

export function useRunEvents(runId: string): RunEventsState {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const eventsRef = useRef<RunEvent[]>([]);

  function replaceEvents(nextEvents: RunEvent[]) {
    eventsRef.current = nextEvents;
    setEvents(nextEvents);
  }

  function appendEvent(event: RunEvent) {
    replaceEvents(mergeEvents(eventsRef.current, [event]));
  }

  useEffect(() => {
    if (!runId) {
      replaceEvents([]);
      setLoading(false);
      setError("");
      return;
    }

    let cancelled = false;

    async function loadAllEvents() {
      setLoading(true);
      setError("");
      try {
        let afterSeq = 0;
        let collected: RunEvent[] = [];
        while (true) {
          const batch = await api.listRunEvents(runId, { afterSeq, limit: EVENT_PAGE_SIZE });
          if (cancelled) {
            return;
          }
          if (batch.length === 0) {
            break;
          }
          collected = mergeEvents(collected, batch);
          afterSeq = Math.max(afterSeq, batch[batch.length - 1]?.seq ?? afterSeq);
          if (batch.length < EVENT_PAGE_SIZE) {
            break;
          }
        }
        if (!cancelled) {
          replaceEvents(collected);
        }
      } catch (err) {
        if (!cancelled) {
          setError((err as Error).message);
          replaceEvents([]);
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void loadAllEvents();

    return () => {
      cancelled = true;
    };
  }, [runId]);

  return {
    events,
    highestSeqSeen: events[events.length - 1]?.seq ?? 0,
    loading,
    error,
    appendEvent,
  };
}
