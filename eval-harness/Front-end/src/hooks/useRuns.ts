import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { RunListItem } from "../types";

export function useRuns() {
	const [runs, setRuns] = useState<RunListItem[]>([]);
	const [total, setTotal] = useState(0);
	const [page, setPage] = useState(1);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const pageSize = 50;

	const fetch = useCallback((p: number) => {
		setLoading(true);
		setError(null);
		api
			.listRuns({ page: p, page_size: pageSize })
			.then((res) => {
				setRuns(res.items);
				setTotal(res.total);
				setPage(res.page);
			})
			.catch((e) => setError(e.message || "Failed to load runs"))
			.finally(() => setLoading(false));
	}, []);

	useEffect(() => {
		fetch(1);
	}, [fetch]);

	return {
		runs,
		total,
		page,
		pageSize,
		loading,
		error,
		setPage: (p: number) => fetch(p),
		refetch: () => fetch(page),
	};
}
