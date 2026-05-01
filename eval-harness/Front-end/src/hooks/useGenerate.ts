import { useState } from "react";
import { api } from "../api";
import type {
	CreateRevisionRequest,
	GenerateRequest,
	ValidateResponse,
} from "../types";

export function useGenerate() {
	const [generating, setGenerating] = useState(false);
	const [validating, setValidating] = useState(false);
	const [saving, setSaving] = useState(false);
	const [scenario, setScenario] = useState<Record<string, unknown> | null>(
		null,
	);
	const [validation, setValidation] = useState<ValidateResponse | null>(null);
	const [error, setError] = useState<string | null>(null);

	const generate = async (req: GenerateRequest) => {
		setGenerating(true);
		setError(null);
		setValidation(null);
		try {
			const res = await api.generateScenario(req);
			setScenario(res.scenario);
		} catch (e) {
			setError(e instanceof Error ? e.message : "Generation failed");
		} finally {
			setGenerating(false);
		}
	};

	const validate = async (json: Record<string, unknown>) => {
		setValidating(true);
		setError(null);
		try {
			const res = await api.validateScenario({ scenario_json: json });
			setValidation(res);
			return res;
		} catch (e) {
			setError(e instanceof Error ? e.message : "Validation failed");
			return null;
		} finally {
			setValidating(false);
		}
	};

	const saveAsRevision = async (
		scenarioId: string,
		json: Record<string, unknown>,
	) => {
		setSaving(true);
		setError(null);
		try {
			const body: CreateRevisionRequest = {
				target_image: (json.target_image as string) || "",
				summary: (json.summary as string) || "",
				what_it_tests: (json.what_it_tests as Record<string, unknown>) || {},
				observable_problem_statement:
					(json.observable_problem_statement as string) || "",
				initial_user_message: (json.initial_user_message as string) || "",
				sabotage_plan:
					(json.sabotage_plan as Record<string, unknown>) ||
					(json.sabotage_procedure
						? {
								steps: json.sabotage_procedure,
							}
						: {}),
				verification_plan:
					(json.verification_plan as Record<string, unknown>) ||
					(json.verification_probes
						? {
								probes: json.verification_probes,
							}
						: {}),
				judge_rubric: (json.judge_rubric as Record<string, unknown>) || {},
				planner_metadata:
					(json.planner_metadata as Record<string, unknown>) || null,
			};
			return await api.createRevision(scenarioId, body);
		} catch (e) {
			setError(e instanceof Error ? e.message : "Save failed");
			return null;
		} finally {
			setSaving(false);
		}
	};

	const updateScenario = (json: Record<string, unknown>) => {
		setScenario(json);
		setValidation(null);
	};

	return {
		generating,
		validating,
		saving,
		scenario,
		validation,
		error,
		generate,
		validate,
		saveAsRevision,
		updateScenario,
		setError,
	};
}
