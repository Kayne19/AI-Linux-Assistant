import { useEffect, useState } from "react";

import { api } from "../api";
import { useGenerateStream } from "../hooks/useGenerateStream";
import type { CreateRevisionRequest } from "../types";

type Props = {
	sourceScenarioId?: string;
	onSaved: (newScenarioId: string) => void;
	onDiscard: () => void;
};

export function NewScenarioPage({
	sourceScenarioId,
	onSaved,
	onDiscard,
}: Props) {
	const [brief, setBrief] = useState("");
	const [targetImage, setTargetImage] = useState("");
	const [nameHint, setNameHint] = useState("");
	const [tags, setTags] = useState("");
	const stream = useGenerateStream();
	const [saving, setSaving] = useState(false);

	useEffect(() => {
		if (!sourceScenarioId) return;
		api.getScenario(sourceScenarioId).then((s) => {
			if (s.title) setBrief(s.title);
			if (s.revisions?.[0]?.target_image) {
				setTargetImage(s.revisions[0].target_image);
			}
			setNameHint(s.scenario_name ?? "");
		});
	}, [sourceScenarioId]);

	const startGenerate = () => {
		stream.start({
			planning_brief: brief,
			target_image: targetImage || undefined,
			scenario_name_hint: nameHint || undefined,
			tags: tags
				.split(",")
				.map((t) => t.trim())
				.filter(Boolean),
			constraints: [],
		});
	};

	const save = async () => {
		if (!stream.scenario) return;
		setSaving(true);
		try {
			const spec = stream.scenario;
			const title =
				(spec.title as string) ||
				(spec.scenario_name as string) ||
				"New scenario";
			const scenarioNameHint = (spec.scenario_name as string) || "";

			const created = await api.createScenario({
				title,
				scenario_name_hint: scenarioNameHint,
			});

			const body: CreateRevisionRequest = {
				target_image: (spec.target_image as string) || "",
				summary: (spec.summary as string) || "",
				what_it_tests: (spec.what_it_tests as Record<string, unknown>) || {},
				observable_problem_statement:
					(spec.observable_problem_statement as string) || "",
				initial_user_message: (spec.initial_user_message as string) || "",
				sabotage_plan:
					(spec.sabotage_plan as Record<string, unknown>) ||
					(spec.sabotage_procedure ? { steps: spec.sabotage_procedure } : {}),
				verification_plan:
					(spec.verification_plan as Record<string, unknown>) ||
					(spec.verification_probes
						? { probes: spec.verification_probes }
						: {}),
				judge_rubric: (spec.judge_rubric as Record<string, unknown>) || {},
				planner_metadata:
					(spec.planner_metadata as Record<string, unknown>) || null,
			};
			await api.createRevision(created.id, body);
			onSaved(created.id);
		} finally {
			setSaving(false);
		}
	};

	return (
		<section className="new-scenario-page">
			<header>
				<h1>New Scenario</h1>
				<button type="button" onClick={onDiscard}>
					Discard
				</button>
			</header>

			<div className="new-scenario-page__body">
				<form
					className="new-scenario-form"
					onSubmit={(e) => {
						e.preventDefault();
						startGenerate();
					}}
				>
					<label>
						Planning brief
						<textarea
							value={brief}
							onChange={(e) => setBrief(e.target.value)}
							rows={6}
							required
						/>
					</label>
					<label>
						Target image
						<input
							value={targetImage}
							onChange={(e) => setTargetImage(e.target.value)}
						/>
					</label>
					<label>
						Name hint
						<input
							value={nameHint}
							onChange={(e) => setNameHint(e.target.value)}
						/>
					</label>
					<label>
						Tags (comma-separated)
						<input value={tags} onChange={(e) => setTags(e.target.value)} />
					</label>
					<div className="new-scenario-form__actions">
						<button
							type="submit"
							className="primary"
							disabled={stream.status === "streaming"}
						>
							{stream.status === "streaming" ? "Generating\u2026" : "Generate"}
						</button>
						{stream.status === "streaming" && (
							<button type="button" onClick={stream.cancel}>
								Cancel
							</button>
						)}
					</div>
				</form>

				<aside className="new-scenario-output">
					<h3>Live output</h3>
					<pre className="stream-pre">{stream.text || "(no output yet)"}</pre>
					{stream.status === "error" && (
						<div className="error-banner">{stream.error}</div>
					)}
					{stream.scenario && (
						<div className="new-scenario-output__save">
							<button
								type="button"
								className="primary"
								onClick={save}
								disabled={saving}
							>
								{saving ? "Saving\u2026" : "Save as new scenario"}
							</button>
						</div>
					)}
				</aside>
			</div>
		</section>
	);
}
