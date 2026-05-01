import { useCallback, useState } from "react";
import { api } from "../api";
import type { PreflightResponse } from "../types";

export function usePreflight() {
	const [result, setResult] = useState<PreflightResponse | null>(null);
	const [loading, setLoading] = useState(false);

	const runPreflight = useCallback(async () => {
		setLoading(true);
		try {
			const data = await api.preflight();
			setResult(data);
		} catch (err) {
			const msg = err instanceof Error ? err.message : "Preflight check failed";
			setResult({ ok: false, message: msg });
		} finally {
			setLoading(false);
		}
	}, []);

	return { result, loading, runPreflight };
}
