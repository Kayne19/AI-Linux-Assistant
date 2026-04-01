import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { ChatRun } from "../types";

export function useRunSnapshot(runId: string) {
  const [run, setRun] = useState<ChatRun | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const requestIdRef = useRef(0);

  useEffect(() => {
    if (!runId) {
      setRun(null);
      setLoading(false);
      setError("");
      return;
    }

    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    setLoading(true);
    setError("");

    void api.getRun(runId)
      .then((nextRun) => {
        if (requestIdRef.current !== requestId) {
          return;
        }
        setRun(nextRun);
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

  async function refresh() {
    if (!runId) {
      return;
    }
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    setError("");
    try {
      const nextRun = await api.getRun(runId);
      if (requestIdRef.current !== requestId) {
        return;
      }
      setRun(nextRun);
    } catch (err) {
      if (requestIdRef.current !== requestId) {
        return;
      }
      setError((err as Error).message);
    }
  }

  return {
    run,
    loading,
    error,
    refresh,
  };
}
