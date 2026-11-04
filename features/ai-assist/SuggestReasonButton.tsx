"use client";

import { useState } from "react";
import { Button } from "@/components";
import { useSuggestModerationReason } from "./hooks";
import { useAiAssistPanelStore } from "./store";
import type { AiAssistActionType } from "./types";

/* Shared AI reason-suggestion button lives HERE ONLY (SuggestReasonButton).
   Used by every reject/revoke surface; fills the parent reason field. */
export function SuggestReasonButton({
  listingId,
  actionType,
  onDraft,
  disabled,
}: {
  listingId: number | null;
  actionType: AiAssistActionType;
  onDraft: (draft: string) => void;
  disabled?: boolean;
}) {
  const suggest = useSuggestModerationReason();
  const setPanelOpen = useAiAssistPanelStore((state) => state.setOpen);
  const [filledKey, setFilledKey] = useState<string | null>(null);
  const filled = filledKey !== null && filledKey === `${actionType}:${listingId}`;

  const handleClick = async () => {
    if (listingId === null) return;
    try {
      const result = await suggest.mutateAsync({ listingId, actionType });
      onDraft(result.draft);
      setFilledKey(`${actionType}:${listingId}`);
    } catch {
      // Error toasted by the hook.
    }
  };

  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <Button
        type="button"
        variant="secondary"
        size="sm"
        loading={suggest.isPending}
        disabled={disabled || listingId === null}
        onClick={() => void handleClick()}
      >
        Suggest reason
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        onClick={() => setPanelOpen(true)}
      >
        AI details
      </Button>
      {filled ? (
        <span className="text-xs text-gray-500 dark:text-gray-400">
          AI-drafted - review before submitting
        </span>
      ) : null}
    </span>
  );
}
