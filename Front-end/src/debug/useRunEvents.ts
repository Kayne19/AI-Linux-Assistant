import { useEffect, useState } from "react";

import { api } from "../api";
import type { RunEvent } from "../types";
import { getHighestSeq, mergeRunEvents } from "./debugUtils";

const EVENT_BATCH_SIZE = 200;

export function useRunEvents(runId: string) {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!runId) {
      setEvents([]);
      setLoading(false);
      setError("");
      return;
    }

    let cancelled = false;
    setEvents([]);
    setLoading(true);
    setError("");

    void (async () => {
      try {
        let merged: RunEvent[] = [];
        let cursor = 0;
        while (true) {
          const batch = await api.listRunEvents(runId, { afterSeq: cursor, limit: EVENT_BATCH_SIZE });
          if (cancelled) {
            return;
          }
          if (batch.length === 0) {
            break;
          }
          merged = mergeRunEvents(merged, batch);
          cursor = getHighestSeq(merged);
          if (batch.length < EVENT_BATCH_SIZE) {
            break;
          }
        }
        if (cancelled) {
          return;
        }
        setEvents(merged);
        setLoading(false);
      } catch (nextError) {
        if (cancelled) {
          return;
        }
        setError((nextError as Error).message);
        setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [runId]);

  function appendEvent(event: RunEvent) {
    setEvents((current) => mergeRunEvents(current, [event]));
  }

  function appendEvents(nextEvents: RunEvent[]) {
    setEvents((current) => mergeRunEvents(current, nextEvents));
  }

  return {
    events,
    loading,
    error,
    highestSeqSeen: getHighestSeq(events),
    appendEvent,
    appendEvents,
  };
}
