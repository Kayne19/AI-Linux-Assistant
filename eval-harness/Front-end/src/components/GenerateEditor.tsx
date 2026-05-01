import { useState } from "react";
import { useGenerate } from "../hooks/useGenerate";
import type { GenerateRequest } from "../types";

type Props = {
	scenarioId?: string;
	onRevisionSaved?: () => void;
};

const TARGET_IMAGES = [
	"ubuntu-2204",
	"ubuntu-2404",
	"debian-12",
	"amazon-linux-2023",
];

export default function GenerateEditor({ scenarioId, onRevisionSaved }: Props) {
	const gen = useGenerate();
	const [planningBrief, setPlanningBrief] = useState("");
	const [targetImage, setTargetImage] = useState("ubuntu-2404");
	const [scenarioNameHint, setScenarioNameHint] = useState("");
	const [tagsInput, setTagsInput] = useState("");
	const [constraintsInput, setConstraintsInput] = useState("");
	const [jsonText, setJsonText] = useState("");
	const [saveMessage, setSaveMessage] = useState<string | null>(null);

	const handleGenerate = async () => {
		if (!planningBrief.trim()) return;
		const tags = tagsInput
			.split(",")
			.map((t) => t.trim())
			.filter(Boolean);
		const constraints = constraintsInput
			.split("\n")
			.map((c) => c.trim())
			.filter(Boolean);
		const req: GenerateRequest = {
			planning_brief: planningBrief.trim(),
			target_image: targetImage || undefined,
			scenario_name_hint: scenarioNameHint.trim() || undefined,
			tags: tags.length > 0 ? tags : undefined,
			constraints: constraints.length > 0 ? constraints : undefined,
		};
		await gen.generate(req);
	};

	// Sync scenario to JSON text area when scenario changes
	const scenarioJson = gen.scenario;
	const displayJson =
		scenarioJson != null ? JSON.stringify(scenarioJson, null, 2) : jsonText;

	const handleJsonChange = (value: string) => {
		setJsonText(value);
		try {
			const parsed = JSON.parse(value);
			gen.updateScenario(parsed);
		} catch {
			// Invalid JSON while typing; keep the text but don't update scenario
		}
	};

	const handleValidate = () => {
		const json =
			scenarioJson ??
			(() => {
				try {
					return JSON.parse(jsonText);
				} catch {
					return null;
				}
			})();
		if (json) {
			gen.validate(json as Record<string, unknown>);
		}
	};

	const handleSave = async () => {
		if (!scenarioId) {
			gen.setError("Select a scenario first before saving a revision.");
			return;
		}
		const json =
			scenarioJson ??
			(() => {
				try {
					return JSON.parse(jsonText);
				} catch {
					return null;
				}
			})();
		if (!json) {
			gen.setError("No valid scenario JSON to save.");
			return;
		}
		const result = await gen.saveAsRevision(
			scenarioId,
			json as Record<string, unknown>,
		);
		if (result) {
			setSaveMessage(
				`Revision #${result.revision_number} saved (${result.id.slice(0, 8)}...)`,
			);
			onRevisionSaved?.();
		}
	};

	return (
		<div className="generate-editor">
			<div className="generate-panes">
				{/* Left pane — form */}
				<div className="generate-form-pane">
					<h3 className="generate-pane-title">Planning Form</h3>

					<label className="generate-label">
						Planning Brief
						<textarea
							className="generate-textarea"
							rows={6}
							placeholder="Describe the scenario you want the planner to generate..."
							value={planningBrief}
							onChange={(e) => setPlanningBrief(e.target.value)}
						/>
					</label>

					<label className="generate-label">
						Target Image
						<select
							className="generate-select"
							value={targetImage}
							onChange={(e) => setTargetImage(e.target.value)}
						>
							<option value="">(auto)</option>
							{TARGET_IMAGES.map((img) => (
								<option key={img} value={img}>
									{img}
								</option>
							))}
						</select>
					</label>

					<label className="generate-label">
						Scenario Name Hint
						<input
							className="generate-input"
							type="text"
							placeholder="e.g. sshd-config-broken"
							value={scenarioNameHint}
							onChange={(e) => setScenarioNameHint(e.target.value)}
						/>
					</label>

					<label className="generate-label">
						Tags (comma-separated)
						<input
							className="generate-input"
							type="text"
							placeholder="e.g. ssh, networking, security"
							value={tagsInput}
							onChange={(e) => setTagsInput(e.target.value)}
						/>
					</label>

					<label className="generate-label">
						Constraints (one per line)
						<textarea
							className="generate-textarea"
							rows={3}
							placeholder="e.g. Use only systemd-based sabotage"
							value={constraintsInput}
							onChange={(e) => setConstraintsInput(e.target.value)}
						/>
					</label>

					<button
						className="generate-btn primary"
						disabled={gen.generating || !planningBrief.trim()}
						onClick={handleGenerate}
					>
						{gen.generating ? "Generating..." : "Generate"}
					</button>
				</div>

				{/* Right pane — JSON editor */}
				<div className="generate-json-pane">
					<div className="generate-json-header">
						<h3 className="generate-pane-title">Scenario JSON</h3>
						<div className="generate-json-actions">
							<button
								className="generate-btn"
								disabled={gen.validating || !(scenarioJson ?? jsonText)}
								onClick={handleValidate}
							>
								{gen.validating ? "Validating..." : "Validate"}
							</button>
							<button
								className="generate-btn primary"
								disabled={
									gen.saving || !scenarioId || !(scenarioJson ?? jsonText)
								}
								onClick={handleSave}
							>
								{gen.saving ? "Saving..." : "Save Revision"}
							</button>
						</div>
					</div>

					<textarea
						className="generate-json-textarea"
						spellCheck={false}
						value={displayJson}
						onChange={(e) => handleJsonChange(e.target.value)}
						placeholder='Click "Generate" to produce a scenario spec...'
					/>

					{gen.validation && (
						<div
							className={`generate-validation ${gen.validation.valid ? "valid" : "invalid"}`}
						>
							{gen.validation.valid ? (
								<span className="generate-validation-ok">
									Scenario is valid
								</span>
							) : (
								<>
									<span className="generate-validation-err">
										{gen.validation.errors.length} issue
										{gen.validation.errors.length !== 1 ? "s" : ""}:
									</span>
									<ul className="generate-validation-list">
										{gen.validation.errors.map((err, i) => (
											<li key={i}>{err}</li>
										))}
									</ul>
								</>
							)}
						</div>
					)}

					{gen.error && <div className="generate-error">{gen.error}</div>}

					{saveMessage && (
						<div className="generate-save-msg">{saveMessage}</div>
					)}
				</div>
			</div>
		</div>
	);
}
