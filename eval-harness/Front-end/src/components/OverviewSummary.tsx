import type { ScenarioDetail } from "../types";

type Props = {
	scenario: ScenarioDetail;
	onRunBenchmark: () => void;
	onEditJson: () => void;
	onGenerateVariant: () => void;
};

export function OverviewSummary({
	scenario,
	onRunBenchmark,
	onEditJson,
	onGenerateVariant,
}: Props) {
	const latestRevision = scenario.revisions?.[0];

	return (
		<section className="overview-summary">
			<div className="overview-summary__row">
				<Metric
					label="Verification"
					value={scenario.verification_status ?? "unverified"}
				/>
				<Metric
					label="Runs"
					value={String(scenario.benchmark_run_count ?? 0)}
				/>
				<Metric
					label="Last verified"
					value={
						scenario.last_verified_at
							? new Date(scenario.last_verified_at).toLocaleString()
							: "never"
					}
				/>
				<Metric
					label="Lifecycle"
					value={scenario.lifecycle_status ?? "\u2014"}
				/>
			</div>

			{latestRevision && (
				<div className="overview-summary__revision">
					<h3>Latest revision</h3>
					<p>{latestRevision.summary || "(no summary)"}</p>
					<p className="muted">
						Target image: <code>{latestRevision.target_image ?? "\u2014"}</code>
					</p>
				</div>
			)}

			<div className="overview-summary__actions">
				<button type="button" className="primary" onClick={onRunBenchmark}>
					Run benchmark
				</button>
				<button type="button" onClick={onEditJson}>
					Edit JSON
				</button>
				<button type="button" onClick={onGenerateVariant}>
					Generate variant
				</button>
			</div>
		</section>
	);
}

function Metric({ label, value }: { label: string; value: string }) {
	return (
		<div className="metric">
			<div className="metric__label">{label}</div>
			<div className="metric__value">{value}</div>
		</div>
	);
}
