import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../lib/api";
import type { AppSettings, ProviderInfo } from "../../lib/types";

export function useSettings() {
  return useQuery({ queryKey: ["settings"], queryFn: () => api<AppSettings>("/api/settings") });
}

export function useUpdateSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: Partial<AppSettings> | Record<string, unknown>) =>
      api<AppSettings>("/api/settings", { method: "PATCH", body: JSON.stringify(patch) }),
    onSuccess: (next) => qc.setQueryData(["settings"], next),
  });
}

export function useProviders() {
  return useQuery({ queryKey: ["providers"], queryFn: () => api<ProviderInfo[]>("/api/providers") });
}

export function useModels(provider: string, enabled: boolean) {
  return useQuery({
    queryKey: ["models", provider],
    queryFn: () => api<{ models: string[] }>(`/api/providers/${provider}/models`).then((r) => r.models),
    enabled,
    staleTime: 5 * 60_000,
    retry: false,
  });
}
