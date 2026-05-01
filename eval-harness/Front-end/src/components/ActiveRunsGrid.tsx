import type { ScenarioListItem } from "../types";

function statusDot(state: string) {
	const accent = state === "running" ? "var(--green)" : "var(--muted)";
	return (
		<span
			style={{
				width: 7,
				height: 7,
				borderRadius: 999,
				background: accent,
				display: "inline-block",
				flexShrink: 0,
			}}
		/>
	);
}

export default function ActiveRunsGrid({
	scenarios,
}: {
	scenarios: ScenarioListItem[];
}) {
	const active = scenarios.filter(
		(s) => s.lifecycle_status === "running" || s.lifecycle_status === "pending",
	);

	if (active.length === 0) {
		return (
			<div
				className="empty-state"
				style={{ padding: "1.5rem", minHeight: 120 }}
			>
				<p className="lede" style={{ margin: 0 }}>
					No active benchmark runs
				</p>
			</div>
		);
	}

	return (
		<div
			style={{
				display: "grid",
				gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
				gap: 10,
			}}
		>
			{active.map((s) => (
				<div
					key={s.id}
					style={{
						padding: 12,
						border: "1px solid var(--border)",
						borderRadius: 6,
						background: "var(--well)",
					}}
				>
					<div
						style={{
							display: "flex",
							alignItems: "center",
							gap: 8,
							marginBottom: 8,
						}}
					>
						{statusDot(s.lifecycle_status)}
						<strong
							style={{
								fontSize: 13,
								fontFamily: "var(--sans)",
								fontWeight: 500,
								color: "var(--text)",
								overflow: "hidden",
								textOverflow: "ellipsis",
								whiteSpace: "nowrap",
							}}
						>
							{s.title || s.scenario_name}
						</strong>
					</div>
					<div
						style={{
							display: "flex",
							gap: 16,
							fontFamily: "var(--mono)",
							fontSize: 11,
							color: "var(--muted)",
						}}
					>
						<span>{s.lifecycle_status}</span>
						{s.benchmark_run_count > 0 && (
							<span>{s.benchmark_run_count} run(s)</span>
						)}
					</div>
				</div>
			))}
		</div>
	);
}
