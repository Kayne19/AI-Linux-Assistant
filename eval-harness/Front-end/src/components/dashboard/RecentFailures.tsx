import { useState } from "react";

import { api } from "../../api";
import { useRecentFailures } from "../../hooks/useRecentFailures";

export function RecentFailures({
	onSelectScenario,
}: {
	onSelectScenario: (id: string) => void;
}) {
	const { failures, refresh } = useRecentFailures(5);
	const [busy, setBusy] = useState<string | null>(null);

	const retry = async (scenarioId: string) => {
		if (!scenarioId) return;
		setBusy(scenarioId);
		try {
			// Start a new benchmark; the backend will handle setup-run resolution
			await api.benchmarkScenario(scenarioId, { setup_run_id: "" });
		} finally {
			setBusy(null);
			refresh();
		}
	};

	return (
		<div className="widget">
			<div className="widget__title">Recent failures</div>
			{failures.length === 0 && (
				<div
					className="empty-state"
					style={{ padding: "0.5rem", minHeight: 60 }}
				>
					<p className="lede" style={{ margin: 0 }}>
						No recent failures
					</p>
				</div>
			)}
			<ul className="failures-list">
				{failures.map((f) => (
					<li key={f.run_id}>
						{f.scenario_id ? (
							<button
								type="button"
								className="link"
								onClick={() => onSelectScenario(f.scenario_id)}
							>
								{f.scenario_title}
							</button>
						) : (
							<span className="muted">{f.scenario_title}</span>
						)}
						<span className="muted">
							{new Date(f.created_at).toLocaleString()}
						</span>
						<button
							type="button"
							onClick={() => retry(f.scenario_id)}
							disabled={busy === f.scenario_id || !f.scenario_id}
						>
							{busy === f.scenario_id ? "\u2026" : "Retry"}
						</button>
					</li>
				))}
			</ul>
		</div>
	);
}
