"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { ApiError, apiClient } from "@/lib/api/client";
import { notify } from "@/lib/toast";
import { useAiAssistPanelStore } from "./store";
import type {
  AiAssistActionType,
  AiAssistLogEntry,
  AiAssistResponse,
} from "./types";

function suggestionErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError && typeof error.detail === "string") {
    return error.detail;
  }
  return fallback;
}

/* Shared AI suggestion mutations live HERE ONLY (useSuggestModerationReason /
   useSuggestInquiryReply). No fetch() in components per AGENTS.md. */
export function useSuggestModerationReason() {
  const recordSuggestion = useAiAssistPanelStore(
    (state) => state.recordSuggestion,
  );
  return useMutation({
    mutationFn: ({
      listingId,
      actionType,
    }: {
      listingId: number;
      actionType: AiAssistActionType;
    }) =>
      apiClient<AiAssistResponse>("/api/v1/ai-assist/moderation-reason", {
        method: "POST",
        body: JSON.stringify({ listing_id: listingId, action_type: actionType }),
      }),
    onSuccess: (data) => {
      recordSuggestion("moderation-reason", data.meta);
    },
    onError: (error) => {
      notify.error(suggestionErrorMessage(error, "Could not suggest a reason."));
    },
  });
}

export function useSuggestInquiryReply() {
  const recordSuggestion = useAiAssistPanelStore(
    (state) => state.recordSuggestion,
  );
  return useMutation({
    mutationFn: ({ inquiryId }: { inquiryId: number }) =>
      apiClient<AiAssistResponse>("/api/v1/ai-assist/inquiry-reply", {
        method: "POST",
        body: JSON.stringify({ inquiry_id: inquiryId }),
      }),
    onSuccess: (data) => {
      recordSuggestion("inquiry-reply", data.meta);
    },
    onError: (error) => {
      notify.error(suggestionErrorMessage(error, "Could not suggest a reply."));
    },
  });
}

export function useAiAssistLogs(enabled: boolean) {
  return useQuery({
    queryKey: ["aiAssistLogs"],
    queryFn: () =>
      apiClient<AiAssistLogEntry[]>("/api/v1/ai-assist/logs?limit=10"),
    enabled,
    staleTime: 10_000,
    retry: false,
  });
}
