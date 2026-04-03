import { FormEvent, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { AsyncState, Project } from "../types";

type UseProjectsOptions = {
  onStatusChange: (status: AsyncState) => void;
  onError: (message: string) => void;
  onProjectsReloaded?: (projects: Project[]) => void;
};

export function useProjects({
  onStatusChange,
  onError,
  onProjectsReloaded,
}: UseProjectsOptions) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [expandedProjectId, setExpandedProjectId] = useState("");
  const [showCreateProjectDialog, setShowCreateProjectDialog] = useState(false);
  const [editingProjectId, setEditingProjectId] = useState("");
  const [projectNameInput, setProjectNameInput] = useState("");
  const [projectDescriptionInput, setProjectDescriptionInput] = useState("");
  const [editProjectNameInput, setEditProjectNameInput] = useState("");
  const [editProjectDescriptionInput, setEditProjectDescriptionInput] = useState("");

  const selectedProject = useMemo(
    () => projects.find((project) => project.id === selectedProjectId) || null,
    [projects, selectedProjectId],
  );

  useEffect(() => {
    if (!selectedProjectId) {
      setExpandedProjectId("");
      return;
    }

    setExpandedProjectId((current) => {
      if (current && projects.some((project) => project.id === current)) {
        return current;
      }
      return selectedProjectId;
    });
  }, [projects, selectedProjectId]);

  async function reloadProjects() {
    const nextProjects = await api.listProjects();
    setProjects(nextProjects);
    setSelectedProjectId((current) =>
      nextProjects.some((project) => project.id === current) ? current : nextProjects[0]?.id || "",
    );
    onProjectsReloaded?.(nextProjects);
    return nextProjects;
  }

  function openCreateProjectDialog() {
    setShowCreateProjectDialog(true);
  }

  function closeCreateProjectDialog() {
    setShowCreateProjectDialog(false);
  }

  function openEditProjectDialog(project: Project) {
    setEditingProjectId(project.id);
    setEditProjectNameInput(project.name || "");
    setEditProjectDescriptionInput(project.description || "");
    onError("");
  }

  function closeEditProjectDialog() {
    setEditingProjectId("");
    setEditProjectNameInput("");
    setEditProjectDescriptionInput("");
  }

  async function handleCreateProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onStatusChange("loading");
    onError("");

    try {
      const project = await api.createProject({
        name: projectNameInput,
        description: projectDescriptionInput,
      });
      await reloadProjects();
      setSelectedProjectId(project.id);
      setProjectNameInput("");
      setProjectDescriptionInput("");
      setShowCreateProjectDialog(false);
      onStatusChange("idle");
      return project;
    } catch (err) {
      onError((err as Error).message);
      onStatusChange("error");
      return null;
    }
  }

  async function handleEditProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editingProjectId) {
      return null;
    }

    onStatusChange("loading");
    onError("");

    try {
      const updatedProject = await api.updateProject(editingProjectId, {
        name: editProjectNameInput,
        description: editProjectDescriptionInput,
      });
      setProjects((current) =>
        current.map((project) => (project.id === updatedProject.id ? updatedProject : project)),
      );
      setSelectedProjectId(updatedProject.id);
      closeEditProjectDialog();
      await reloadProjects();
      onStatusChange("idle");
      return updatedProject;
    } catch (err) {
      onError((err as Error).message);
      onStatusChange("error");
      return null;
    }
  }

  async function handleDeleteProject() {
    if (!editingProjectId) {
      return false;
    }

    onStatusChange("loading");
    onError("");

    try {
      await api.deleteProject(editingProjectId);
      if (selectedProjectId === editingProjectId) {
        setSelectedProjectId("");
      }
      closeEditProjectDialog();
      await reloadProjects();
      onStatusChange("idle");
      return true;
    } catch (err) {
      onError((err as Error).message);
      onStatusChange("error");
      return false;
    }
  }

  return {
    projects,
    setProjects,
    selectedProjectId,
    setSelectedProjectId,
    expandedProjectId,
    setExpandedProjectId,
    showCreateProjectDialog,
    openCreateProjectDialog,
    closeCreateProjectDialog,
    editingProjectId,
    openEditProjectDialog,
    closeEditProjectDialog,
    projectNameInput,
    setProjectNameInput,
    projectDescriptionInput,
    setProjectDescriptionInput,
    editProjectNameInput,
    setEditProjectNameInput,
    editProjectDescriptionInput,
    setEditProjectDescriptionInput,
    selectedProject,
    reloadProjects,
    handleCreateProject,
    handleEditProject,
    handleDeleteProject,
  };
}
