import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import type { RunEvent } from "../types";
import { mergeRunEvents } from "./debugUtils";

const EVENT_PAGE_SIZE = 1000;

async function loadAllRunEvents(runId: string, afterSeq: number): Promise<RunEvent[]> {
  const events: RunEvent[] = [];
  let cursor = Math.max(0, afterSeq);

  while (true) {
    const nextPage = await api.listRunEvents(runId, { afterSeq: cursor, limit: EVENT_PAGE_SIZE });
    if (nextPage.length === 0) {
      break;
    }
    events.push(...nextPage);
    cursor = nextPage[nextPage.length - 1].seq;
    if (nextPage.length < EVENT_PAGE_SIZE) {
      break;
    }
  }

  return events;
}

export function useRunEvents(runId: string) {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const requestIdRef = useRef(0);
  const highestSeqSeen = useMemo(() => {
    if (events.length === 0) {
      return 0;
    }
    return events[events.length - 1].seq;
  }, [events]);

  useEffect(() => {
    if (!runId) {
      setEvents([]);
      setLoading(false);
      setError("");
      return;
    }

    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    setLoading(true);
    setError("");
    setEvents([]);

    void loadAllRunEvents(runId, 0)
      .then((initialEvents) => {
        if (requestIdRef.current !== requestId) {
          return;
        }
        setEvents(initialEvents);
      })
      .catch((err: Error) => {
        if (requestIdRef.current !== requestId) {
          return;
        }
        setError(err.message);
      })
      .finally(() => {
        if (requestIdRef.current === requestId) {
          setLoading(false);
        }
      });
  }, [runId]);

  function appendLiveEvent(event: RunEvent) {
    setEvents((current) => mergeRunEvents(current, [event]));
  }

  async function backfill(afterSeq: number) {
    if (!runId) {
      return;
    }
    try {
      const missingEvents = await loadAllRunEvents(runId, afterSeq);
      setEvents((current) => mergeRunEvents(current, missingEvents));
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return {
    events,
    loading,
    error,
    highestSeqSeen,
    appendLiveEvent,
    backfill,
  };
}
