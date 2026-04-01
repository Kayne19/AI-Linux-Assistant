import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import type { RunEvent } from "../types";
import { mergeRunEvents } from "./debugUtils";

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

    void api.listRunEvents(runId, { afterSeq: 0 })
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
      const missingEvents = await api.listRunEvents(runId, { afterSeq });
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
