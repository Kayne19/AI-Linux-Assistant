import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { ImageItem } from "../types";

export function useImages() {
	const [images, setImages] = useState<ImageItem[]>([]);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);

	const refresh = useCallback(async () => {
		try {
			setError(null);
			const data = await api.listImages();
			setImages(data);
		} catch (err) {
			const msg = err instanceof Error ? err.message : "Failed to list images";
			setError(msg);
		} finally {
			setLoading(false);
		}
	}, []);

	useEffect(() => {
		refresh();
	}, [refresh]);

	const deregister = useCallback(
		async (imageId: string) => {
			await api.deregisterImage(imageId);
			await refresh();
		},
		[refresh],
	);

	return { images, loading, error, refresh, deregister };
}
