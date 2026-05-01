import type { ScenarioListItem } from "../types";

export default function RunScenarioPicker({
	scenarios,
	onSelect,
}: {
	scenarios: ScenarioListItem[];
	onSelect: (mode: "scenarios", id: string) => void;
}) {
	return (
		<div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
			<select
				onChange={(e) => {
					if (e.target.value) onSelect("scenarios", e.target.value);
				}}
				defaultValue=""
				style={{
					width: "100%",
					border: "1px solid var(--border)",
					borderRadius: 3,
					background: "var(--surface2)",
					color: "var(--text)",
					padding: "0.5rem 0.75rem",
					font: "inherit",
					fontSize: 13,
					cursor: "pointer",
					appearance: "none",
					WebkitAppearance: "none",
					backgroundImage:
						"url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%238a8aa8'/%3E%3C/svg%3E\")",
					backgroundRepeat: "no-repeat",
					backgroundPosition: "right 8px center",
					paddingRight: "1.5rem",
				}}
			>
				<option value="">Run a scenario...</option>
				{scenarios.map((s) => (
					<option key={s.id} value={s.id}>
						{s.title || s.scenario_name}
					</option>
				))}
			</select>
		</div>
	);
}
