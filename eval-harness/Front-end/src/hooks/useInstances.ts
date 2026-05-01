import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { InstanceItem } from "../types";

export function useInstances() {
	const [instances, setInstances] = useState<InstanceItem[]>([]);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);

	const refresh = useCallback(async () => {
		try {
			setError(null);
			const data = await api.listInstances();
			setInstances(data);
		} catch (err) {
			const msg =
				err instanceof Error ? err.message : "Failed to list instances";
			setError(msg);
		} finally {
			setLoading(false);
		}
	}, []);

	useEffect(() => {
		refresh();
	}, [refresh]);

	const terminate = useCallback(
		async (instanceId: string) => {
			await api.terminateInstance(instanceId);
			await refresh();
		},
		[refresh],
	);

	return { instances, loading, error, refresh, terminate };
}
