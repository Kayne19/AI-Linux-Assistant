import Editor from "@monaco-editor/react";
import { useEffect, useState } from "react";

import { api } from "../api";
import type { CreateRevisionRequest } from "../types";

type Props = {
	scenarioId: string;
	initialJson: unknown;
	onRevisionSaved: () => void;
};

export function JsonEditorTab({
	scenarioId,
	initialJson,
	onRevisionSaved,
}: Props) {
	const [text, setText] = useState(() => JSON.stringify(initialJson, null, 2));
	const [validation, setValidation] = useState<
		{ kind: "idle" } | { kind: "valid" } | { kind: "errors"; errors: string[] }
	>({ kind: "idle" });
	const [saving, setSaving] = useState(false);

	useEffect(() => {
		setText(JSON.stringify(initialJson, null, 2));
	}, [initialJson]);

	const validate = async () => {
		let parsed: unknown;
		try {
			parsed = JSON.parse(text);
		} catch (err) {
			setValidation({
				kind: "errors",
				errors: [`Invalid JSON: ${String(err)}`],
			});
			return;
		}
		const result = await api.validateScenario({
			scenario_json: parsed as Record<string, unknown>,
		});
		setValidation(
			result.valid
				? { kind: "valid" }
				: { kind: "errors", errors: result.errors },
		);
	};

	const save = async () => {
		setSaving(true);
		try {
			const parsed = JSON.parse(text) as Record<string, unknown>;
			// Map JSON fields to CreateRevisionRequest following the same pattern as useGenerate.ts
			const body: CreateRevisionRequest = {
				target_image: (parsed.target_image as string) || "",
				summary: (parsed.summary as string) || "",
				what_it_tests: (parsed.what_it_tests as Record<string, unknown>) || {},
				observable_problem_statement:
					(parsed.observable_problem_statement as string) || "",
				initial_user_message: (parsed.initial_user_message as string) || "",
				sabotage_plan:
					(parsed.sabotage_plan as Record<string, unknown>) ||
					(parsed.sabotage_procedure
						? { steps: parsed.sabotage_procedure }
						: {}),
				verification_plan:
					(parsed.verification_plan as Record<string, unknown>) ||
					(parsed.verification_probes
						? { probes: parsed.verification_probes }
						: {}),
				judge_rubric: (parsed.judge_rubric as Record<string, unknown>) || {},
				planner_metadata:
					(parsed.planner_metadata as Record<string, unknown>) || null,
			};
			await api.createRevision(scenarioId, body);
			onRevisionSaved();
		} finally {
			setSaving(false);
		}
	};

	return (
		<section className="json-editor-tab">
			<div className="json-editor-tab__toolbar">
				<button type="button" onClick={validate}>
					Validate
				</button>
				<button
					type="button"
					className="primary"
					onClick={save}
					disabled={saving}
				>
					{saving ? "Saving\u2026" : "Save as revision"}
				</button>
				{validation.kind === "valid" && (
					<span className="ok">Valid \u2713</span>
				)}
				{validation.kind === "errors" && (
					<ul className="errors">
						{validation.errors.map((e) => (
							<li key={e}>{e}</li>
						))}
					</ul>
				)}
			</div>
			<Editor
				height="70vh"
				defaultLanguage="json"
				value={text}
				onChange={(v) => setText(v ?? "")}
				options={{ minimap: { enabled: false }, scrollBeyondLastLine: false }}
			/>
		</section>
	);
}
