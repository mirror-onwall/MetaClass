type PresentationMode = "generated" | "source_deck" | "paper_deck";

export function resolvePresentationMode(workspace: {
  presentationPlan?: { mode?: PresentationMode } | null;
  content?: { organization_mode?: "knowledge" | "source_deck" } | null;
  contentJob?: { organization_mode?: "knowledge" | "source_deck" } | null;
  presentationMode?: PresentationMode;
}): PresentationMode {
  if (workspace.presentationPlan?.mode) return workspace.presentationPlan.mode;
  const organizationMode = workspace.content?.organization_mode
    ?? workspace.contentJob?.organization_mode;
  if (organizationMode === "source_deck") return "source_deck";
  if (workspace.presentationMode === "paper_deck") return "paper_deck";
  if (organizationMode === "knowledge") return "generated";
  return workspace.presentationMode ?? "generated";
}
