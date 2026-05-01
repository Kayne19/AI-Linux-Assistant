import { useImages } from "../../hooks/useImages";
import { useInstances } from "../../hooks/useInstances";

export function AmiInstanceHealth() {
	const { images } = useImages();
	const { instances } = useInstances();

	const healthy = (images ?? []).filter((i) => i.state === "available").length;
	const oldest = (images ?? [])
		.map((i) => (i.created_at ? new Date(i.created_at).getTime() : 0))
		.filter((t) => t > 0)
		.sort((a, b) => a - b)[0];
	const oldestAgeDays = oldest
		? Math.floor((Date.now() - oldest) / 86400000)
		: null;
	const overBudget = (instances ?? []).filter(
		(i) =>
			i.launched_at &&
			Date.now() - new Date(i.launched_at).getTime() > 6 * 3600000,
	).length;

	return (
		<div className="widget">
			<div className="widget__title">Infra health</div>
			<ul className="kvs">
				<li>
					<span>Healthy AMIs</span>
					<strong>{healthy}</strong>
				</li>
				<li>
					<span>Oldest AMI</span>
					<strong>
						{oldestAgeDays != null ? `${oldestAgeDays}d` : "\u2014"}
					</strong>
				</li>
				<li>
					<span>Instances &gt;6h</span>
					<strong>{overBudget}</strong>
				</li>
			</ul>
		</div>
	);
}
