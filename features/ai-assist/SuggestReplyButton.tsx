"use client";

import { useState } from "react";
import { Button } from "@/components";
import { useSuggestInquiryReply } from "./hooks";
import { useAiAssistPanelStore } from "./store";

/* Shared AI reply-suggestion button lives HERE ONLY (SuggestReplyButton).
   Used by the inquiry reply composer; fills the parent draft field. */
export function SuggestReplyButton({
  inquiryId,
  onDraft,
  disabled,
}: {
  inquiryId: number;
  onDraft: (draft: string) => void;
  disabled?: boolean;
}) {
  const suggest = useSuggestInquiryReply();
  const setPanelOpen = useAiAssistPanelStore((state) => state.setOpen);
  const [filledKey, setFilledKey] = useState<number | null>(null);
  const filled = filledKey !== null && filledKey === inquiryId;

  const handleClick = async () => {
    try {
      const result = await suggest.mutateAsync({ inquiryId });
      onDraft(result.draft);
      setFilledKey(inquiryId);
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
        disabled={disabled}
        onClick={() => void handleClick()}
      >
        Suggest reply
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
          AI-drafted - review before sending
        </span>
      ) : null}
    </span>
  );
}
