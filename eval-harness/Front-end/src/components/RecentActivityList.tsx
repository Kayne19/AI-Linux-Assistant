import type { ScenarioListItem } from "../types";

function timeAgo(isoString: string): string {
	const ms = Date.now() - new Date(isoString).getTime();
	const mins = Math.floor(ms / 60000);
	if (mins < 1) return "just now";
	if (mins < 60) return `${mins}m ago`;
	const hrs = Math.floor(mins / 60);
	if (hrs < 24) return `${hrs}h ago`;
	return `${Math.floor(hrs / 24)}d ago`;
}

export default function RecentActivityList({
	scenarios,
}: {
	scenarios: ScenarioListItem[];
}) {
	const recent = [...scenarios]
		.filter(
			(s) =>
				s.lifecycle_status === "completed" || s.lifecycle_status === "failed",
		)
		.sort(
			(a, b) =>
				new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
		)
		.slice(0, 10);

	if (recent.length === 0) {
		return (
			<div className="empty-state" style={{ padding: "1.5rem", minHeight: 80 }}>
				<p className="lede" style={{ margin: 0 }}>
					No recent activity
				</p>
			</div>
		);
	}

	return (
		<div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
			{recent.map((s) => (
				<div
					key={s.id}
					style={{
						display: "flex",
						alignItems: "center",
						justifyContent: "space-between",
						padding: "8px 0",
						borderBottom: "1px solid var(--border)",
						gap: 12,
					}}
				>
					<div
						style={{
							display: "flex",
							alignItems: "center",
							gap: 8,
							minWidth: 0,
						}}
					>
						<span
							style={{
								width: 6,
								height: 6,
								borderRadius: 999,
								background:
									s.lifecycle_status === "completed"
										? "var(--green)"
										: "var(--danger)",
								flexShrink: 0,
							}}
						/>
						<span
							style={{
								fontSize: 13,
								fontWeight: 500,
								color: "var(--text)",
								overflow: "hidden",
								textOverflow: "ellipsis",
								whiteSpace: "nowrap",
							}}
						>
							{s.title || s.scenario_name}
						</span>
					</div>
					<span
						style={{
							fontFamily: "var(--mono)",
							fontSize: 11,
							color: "var(--muted)",
							flexShrink: 0,
						}}
					>
						{timeAgo(s.created_at)}
					</span>
				</div>
			))}
		</div>
	);
}
