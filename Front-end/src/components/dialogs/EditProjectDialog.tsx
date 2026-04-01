import type { FormEvent } from "react";
import type { AsyncState } from "../../types";

type EditProjectDialogProps = {
  projectName: string;
  projectDescription: string;
  error: string;
  status: AsyncState;
  onProjectNameChange: (value: string) => void;
  onProjectDescriptionChange: (value: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onDelete: () => void;
  onClose: () => void;
};

export function EditProjectDialog({
  projectName,
  projectDescription,
  error,
  status,
  onProjectNameChange,
  onProjectDescriptionChange,
  onSubmit,
  onDelete,
  onClose,
}: EditProjectDialogProps) {
  return (
    <div className="dialog-backdrop" onClick={onClose}>
      <div
        className="dialog-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="edit-project-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="dialog-header">
          <div>
            <p className="eyebrow">Edit project</p>
            <h2 id="edit-project-title">Update this project’s details.</h2>
          </div>
          <button type="button" className="icon-button" aria-label="Close edit project dialog" onClick={onClose}>
            ×
          </button>
        </div>
        <form className="dialog-form" onSubmit={onSubmit}>
          <input
            value={projectName}
            onChange={(event) => onProjectNameChange(event.target.value)}
            placeholder="Debian laptop"
            autoFocus
          />
          <textarea
            rows={3}
            value={projectDescription}
            onChange={(event) => onProjectDescriptionChange(event.target.value)}
            placeholder="What this machine or stack is for"
          />
          {error ? <p className="error-banner">{error}</p> : null}
          <div className="dialog-actions">
            <button type="button" className="danger-button compact" onClick={onDelete} disabled={status === "loading"}>
              Delete
            </button>
            <button type="button" className="ghost-button compact" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" disabled={!projectName.trim() || status === "loading"}>
              Save
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
