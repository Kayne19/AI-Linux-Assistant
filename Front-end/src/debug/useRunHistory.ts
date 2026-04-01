import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { ChatRun } from "../types";

const PAGE_SIZE = 20;

function mergeRuns(currentRuns: ChatRun[], nextRuns: ChatRun[]): ChatRun[] {
  const merged = new Map<string, ChatRun>();
  for (const run of currentRuns) {
    merged.set(run.id, run);
  }
  for (const run of nextRuns) {
    merged.set(run.id, run);
  }
  return Array.from(merged.values()).sort((left, right) => {
    const leftTime = Date.parse(left.created_at) || 0;
    const rightTime = Date.parse(right.created_at) || 0;
    if (rightTime !== leftTime) {
      return rightTime - leftTime;
    }
    return right.id.localeCompare(left.id);
  });
}

export function useRunHistory(chatId: string) {
  const [runs, setRuns] = useState<ChatRun[]>([]);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const requestIdRef = useRef(0);

  useEffect(() => {
    if (!chatId) {
      setRuns([]);
      setPage(1);
      setTotal(0);
      setHasMore(false);
      setError("");
      return;
    }

    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    setLoading(true);
    setLoadingMore(false);
    setError("");
    setRuns([]);
    setPage(1);
    setTotal(0);
    setHasMore(false);

    void api.listRuns(chatId, { page: 1, pageSize: PAGE_SIZE })
      .then((response) => {
        if (requestIdRef.current !== requestId) {
          return;
        }
        setRuns(response.runs);
        setTotal(response.total);
        setHasMore(response.has_more);
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
  }, [chatId]);

  async function loadMore() {
    if (!chatId || loading || loadingMore || !hasMore) {
      return;
    }
    const nextPage = page + 1;
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    setLoadingMore(true);
    setError("");
    try {
      const response = await api.listRuns(chatId, { page: nextPage, pageSize: PAGE_SIZE });
      if (requestIdRef.current !== requestId) {
        return;
      }
      setRuns((current) => mergeRuns(current, response.runs));
      setPage(nextPage);
      setTotal(response.total);
      setHasMore(response.has_more);
    } catch (err) {
      if (requestIdRef.current !== requestId) {
        return;
      }
      setError((err as Error).message);
    } finally {
      if (requestIdRef.current === requestId) {
        setLoadingMore(false);
      }
    }
  }

  async function reload() {
    if (!chatId) {
      return;
    }
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    setLoading(true);
    setLoadingMore(false);
    setError("");
    try {
      const response = await api.listRuns(chatId, { page: 1, pageSize: PAGE_SIZE });
      if (requestIdRef.current !== requestId) {
        return;
      }
      setRuns(response.runs);
      setPage(1);
      setTotal(response.total);
      setHasMore(response.has_more);
    } catch (err) {
      if (requestIdRef.current !== requestId) {
        return;
      }
      setError((err as Error).message);
    } finally {
      if (requestIdRef.current === requestId) {
        setLoading(false);
      }
    }
  }

  return {
    runs,
    total,
    hasMore,
    loading,
    loadingMore,
    error,
    loadMore,
    reload,
  };
}
