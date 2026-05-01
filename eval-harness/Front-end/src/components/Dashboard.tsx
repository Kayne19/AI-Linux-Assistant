import { useInstances } from "../hooks/useInstances";
import { usePreflight } from "../hooks/usePreflight";
import type { ScenarioListItem } from "../types";
import ActiveInstancesTable from "./ActiveInstancesTable";
import ActiveRunsGrid from "./ActiveRunsGrid";
import { AmiInstanceHealth } from "./dashboard/AmiInstanceHealth";
import { RecentFailures } from "./dashboard/RecentFailures";
import { RunHistoryChart } from "./dashboard/RunHistoryChart";
import { SuccessRateSparkline } from "./dashboard/SuccessRateSparkline";
import { TopScenarios } from "./dashboard/TopScenarios";
import RecentActivityList from "./RecentActivityList";

export default function Dashboard({
	scenarios,
	onModeChange,
	onNewScenario,
}: {
	scenarios: ScenarioListItem[];
	onModeChange: (mode: "scenarios", id: string) => void;
	onNewScenario?: () => void;
}) {
	const { instances, loading: instancesLoading, terminate } = useInstances();
	const { loading: preflightLoading, runPreflight } = usePreflight();

	return (
		<div className="dashboard">
			<header className="dashboard-header">
				<h1>Control Center</h1>
				<div className="dashboard-quick-actions">
					<button
						type="button"
						className="primary"
						onClick={() => onNewScenario?.()}
					>
						+ New Scenario
					</button>
					<button
						type="button"
						onClick={runPreflight}
						disabled={preflightLoading}
					>
						{preflightLoading ? "Running\u2026" : "Run preflight"}
					</button>
				</div>
			</header>

			<section className="dashboard-grid">
				<SuccessRateSparkline />
				<RunHistoryChart />
				<AmiInstanceHealth />
				<RecentFailures
					onSelectScenario={(id) => onModeChange("scenarios", id)}
				/>
				<TopScenarios onSelect={(id) => onModeChange("scenarios", id)} />
			</section>

			<section className="dashboard-row">
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
			</section>

			<section>
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
			</section>
		</div>
	);
}
