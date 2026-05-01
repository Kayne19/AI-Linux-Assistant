import { useInstances } from "../hooks/useInstances";
import type { ScenarioListItem } from "../types";
import ActiveInstancesTable from "./ActiveInstancesTable";
import ActiveRunsGrid from "./ActiveRunsGrid";
import RecentActivityList from "./RecentActivityList";
import RunScenarioPicker from "./RunScenarioPicker";

export default function Dashboard({
	scenarios,
	onModeChange,
}: {
	scenarios: ScenarioListItem[];
	onModeChange: (mode: "scenarios", id: string) => void;
}) {
	const { instances, loading: instancesLoading, terminate } = useInstances();

	return (
		<div
			style={{ padding: 20, display: "flex", flexDirection: "column", gap: 24 }}
		>
			<div>
				<h3
					style={{
						margin: "0 0 12px",
						fontFamily: "var(--mono)",
						fontSize: 10,
						letterSpacing: "0.12em",
						textTransform: "uppercase",
						color: "var(--accent-text)",
						fontWeight: 500,
					}}
				>
					Active Benchmark Runs
				</h3>
				<ActiveRunsGrid scenarios={scenarios} />
			</div>

			<div>
				<h3
					style={{
						margin: "0 0 12px",
						fontFamily: "var(--mono)",
						fontSize: 10,
						letterSpacing: "0.12em",
						textTransform: "uppercase",
						color: "var(--accent-text)",
						fontWeight: 500,
					}}
				>
					Active AWS Instances
				</h3>
				<ActiveInstancesTable
					instances={instances}
					loading={instancesLoading}
					onTerminate={async (id) => {
						await terminate(id);
					}}
				/>
			</div>

			<div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>
				<div>
					<h3
						style={{
							margin: "0 0 12px",
							fontFamily: "var(--mono)",
							fontSize: 10,
							letterSpacing: "0.12em",
							textTransform: "uppercase",
							color: "var(--accent-text)",
							fontWeight: 500,
						}}
					>
						Recent Activity
					</h3>
					<RecentActivityList scenarios={scenarios} />
				</div>
				<div>
					<h3
						style={{
							margin: "0 0 12px",
							fontFamily: "var(--mono)",
							fontSize: 10,
							letterSpacing: "0.12em",
							textTransform: "uppercase",
							color: "var(--accent-text)",
							fontWeight: 500,
						}}
					>
						Quick Action
					</h3>
					<RunScenarioPicker scenarios={scenarios} onSelect={onModeChange} />
				</div>
			</div>
		</div>
	);
}
