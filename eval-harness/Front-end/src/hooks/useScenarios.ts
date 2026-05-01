import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { ScenarioListItem } from "../types";

export function useScenarios() {
	const [scenarios, setScenarios] = useState<ScenarioListItem[]>([]);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);

	const refresh = useCallback(async () => {
		try {
			setError(null);
			const data = await api.listScenarios();
			setScenarios(data);
		} catch (err) {
			const msg =
				err instanceof Error ? err.message : "Failed to list scenarios";
			setError(msg);
		} finally {
			setLoading(false);
		}
	}, []);

	useEffect(() => {
		refresh();
	}, [refresh]);

	return { scenarios, loading, error, refresh };
}
