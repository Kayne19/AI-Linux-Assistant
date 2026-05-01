import { useCallback, useEffect, useState } from "react";

import { api } from "../api";

type FailureItem = {
	run_id: string;
	scenario_id: string;
	scenario_title: string;
	created_at: string;
	scenario_revision_id: string;
	setup_run_id: string | null;
};

export function useRecentFailures(limit = 5) {
	const [failures, setFailures] = useState<FailureItem[]>([]);

	const refresh = useCallback(() => {
		api
			.listRuns({ page_size: 100 })
			.then((res) => {
				const failed = res.items
					.filter((r) => r.kind === "benchmark" && r.status === "failed")
					.sort(
						(a, b) =>
							new Date(b.created_at).getTime() -
							new Date(a.created_at).getTime(),
					)
					.slice(0, limit);

				// Resolve scenario titles: fetch all scenarios, then match by revision IDs
				api.listScenarios().then((scenarios) => {
					// For each failure, try to find the owning scenario
					// Fetch scenario details in parallel to get revision IDs
					Promise.all(
						scenarios.map((s) => api.getScenario(s.id).catch(() => null)),
					).then((details) => {
						// Build revision → scenario lookup
						const revToScenario = new Map<
							string,
							{ id: string; title: string }
						>();
						for (const d of details) {
							if (!d) continue;
							for (const rev of d.revisions) {
								revToScenario.set(rev.id, {
									id: d.id,
									title: d.title,
								});
							}
						}

						setFailures(
							failed.map((r) => {
								const owner = revToScenario.get(r.scenario_revision_id);
								return {
									run_id: r.id,
									scenario_id: owner?.id ?? "",
									scenario_title:
										owner?.title ?? r.scenario_revision_id.slice(0, 8),
									created_at: r.created_at,
									scenario_revision_id: r.scenario_revision_id,
									setup_run_id: r.verified_setup_run_id ?? null,
								};
							}),
						);
					});
				});
			})
			.catch(() => {
				// Silently handle fetch failures
			});
	}, [limit]);

	useEffect(() => {
		refresh();
	}, [refresh]);

	return { failures, refresh };
}
