import { useMemo, useState } from "react";

import { useScenarios } from "../hooks/useScenarios";
import { ScenarioCard } from "./ScenarioCard";

type Props = {
	onSelectScenario: (id: string) => void;
	onNewScenario: () => void;
};

const STATUS_FILTERS = [
	"all",
	"draft",
	"verified",
	"running",
	"failed",
] as const;

export function ScenariosGrid({ onSelectScenario, onNewScenario }: Props) {
	const { scenarios, loading: isLoading, error, refresh } = useScenarios();
	const [search, setSearch] = useState("");
	const [status, setStatus] = useState<(typeof STATUS_FILTERS)[number]>("all");
	const [sort, setSort] = useState<"updated" | "name" | "runs">("updated");

	const filtered = useMemo(() => {
		let list = scenarios ?? [];
		if (status !== "all")
			list = list.filter((s) => s.lifecycle_status === status);
		if (search.trim()) {
			const q = search.trim().toLowerCase();
			list = list.filter(
				(s) =>
					s.title.toLowerCase().includes(q) ||
					(s.scenario_name?.toLowerCase().includes(q) ?? false) ||
					(s.tags ?? []).some((t) => t.toLowerCase().includes(q)),
			);
		}
		return [...list].sort((a, b) => {
			if (sort === "name") return a.title.localeCompare(b.title);
			if (sort === "runs")
				return (
					(b.run_count ?? b.benchmark_run_count ?? 0) -
					(a.run_count ?? a.benchmark_run_count ?? 0)
				);
			return (b.updated_at ?? "").localeCompare(a.updated_at ?? "");
		});
	}, [scenarios, search, status, sort]);

	return (
		<section className="scenarios-grid">
			<header className="scenarios-grid__header">
				<div className="scenarios-grid__title">
					<h1>Scenarios</h1>
					<button type="button" onClick={refresh} disabled={isLoading}>
						Refresh
					</button>
				</div>
				<div className="scenarios-grid__controls">
					<input
						type="search"
						placeholder="Search title, name, tag…"
						value={search}
						onChange={(e) => setSearch(e.target.value)}
					/>
					<div className="status-chips">
						{STATUS_FILTERS.map((s) => (
							<button
								key={s}
								type="button"
								className={`chip ${status === s ? "chip--active" : ""}`}
								onClick={() => setStatus(s)}
							>
								{s}
							</button>
						))}
					</div>
					<select
						value={sort}
						onChange={(e) => setSort(e.target.value as typeof sort)}
					>
						<option value="updated">Recently updated</option>
						<option value="name">Name</option>
						<option value="runs">Run count</option>
					</select>
					<button type="button" className="primary" onClick={onNewScenario}>
						+ New Scenario
					</button>
				</div>
			</header>

			{error ? <div className="error-banner">{String(error)}</div> : null}

			<div className="scenarios-grid__cards">
				{filtered.map((s) => (
					<ScenarioCard key={s.id} scenario={s} onSelect={onSelectScenario} />
				))}
				{filtered.length === 0 && !isLoading ? (
					<div className="empty-state">
						No scenarios match.{" "}
						<button type="button" onClick={onNewScenario}>
							Create one
						</button>
						.
					</div>
				) : null}
			</div>
		</section>
	);
}
