import type { FormEvent } from "react";
import type { AsyncState } from "../../types";

type EditChatDialogProps = {
  chatTitle: string;
  error: string;
  status: AsyncState;
  onChatTitleChange: (value: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onDelete: () => void;
  onClose: () => void;
};

export function EditChatDialog({
  chatTitle,
  error,
  status,
  onChatTitleChange,
  onSubmit,
  onDelete,
  onClose,
}: EditChatDialogProps) {
  return (
    <div className="dialog-backdrop" onClick={onClose}>
      <div
        className="dialog-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="edit-chat-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="dialog-header">
          <div>
            <p className="eyebrow">Edit chat</p>
            <h2 id="edit-chat-title">Update this chat’s details.</h2>
          </div>
          <button type="button" className="icon-button" aria-label="Close edit chat dialog" onClick={onClose}>
            ×
          </button>
        </div>
        <form className="dialog-form" onSubmit={onSubmit}>
          <input
            value={chatTitle}
            onChange={(event) => onChatTitleChange(event.target.value)}
            placeholder="Fresh troubleshooting session"
            autoFocus
          />
          {error ? <p className="error-banner">{error}</p> : null}
          <div className="dialog-actions">
            <button type="button" className="danger-button compact" onClick={onDelete} disabled={status === "loading"}>
              Delete
            </button>
            <button type="button" className="ghost-button compact" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" disabled={!chatTitle.trim() || status === "loading"}>
              Save
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
