import { useEffect, useState } from "react";

import { api } from "../api";
import type { ChatRun } from "../types";

export function useRunSnapshot(runId: string, initialRun: ChatRun | null) {
  const [run, setRun] = useState<ChatRun | null>(initialRun);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setRun(initialRun);
  }, [initialRun, runId]);

  useEffect(() => {
    if (!runId) {
      setRun(null);
      setLoading(false);
      setError("");
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError("");

    void api.getRun(runId)
      .then((nextRun) => {
        if (cancelled) {
          return;
        }
        setRun(nextRun);
        setLoading(false);
      })
      .catch((nextError: Error) => {
        if (cancelled) {
          return;
        }
        setError(nextError.message);
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [runId]);

  async function refresh() {
    if (!runId) {
      return null;
    }
    setError("");
    try {
      const nextRun = await api.getRun(runId);
      setRun(nextRun);
      return nextRun;
    } catch (nextError) {
      setError((nextError as Error).message);
      return null;
    }
  }

  return {
    run,
    setRun,
    loading,
    error,
    refresh,
  };
}
