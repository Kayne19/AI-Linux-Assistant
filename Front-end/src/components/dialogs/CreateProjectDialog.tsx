import type { FormEvent } from "react";
import type { AsyncState } from "../../types";

type CreateProjectDialogProps = {
  projectName: string;
  projectDescription: string;
  status: AsyncState;
  onProjectNameChange: (value: string) => void;
  onProjectDescriptionChange: (value: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onClose: () => void;
};

export function CreateProjectDialog({
  projectName,
  projectDescription,
  status,
  onProjectNameChange,
  onProjectDescriptionChange,
  onSubmit,
  onClose,
}: CreateProjectDialogProps) {
  return (
    <div className="dialog-backdrop" onClick={onClose}>
      <div
        className="dialog-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-project-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="dialog-header">
          <div>
            <p className="eyebrow">New project</p>
            <h2 id="create-project-title">Create a project workspace.</h2>
          </div>
          <button type="button" className="icon-button" aria-label="Close project dialog" onClick={onClose}>
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
          <div className="dialog-actions">
            <button type="button" className="ghost-button compact" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" disabled={!projectName.trim() || status === "loading"}>
              Create
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
