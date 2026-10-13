import type { PluginClientContext, PluginWorkspaceCommandContext } from "@getpaseo/plugin/client";
import { OpsPanel } from "./client/panel";

export default function contribute(client: PluginClientContext) {
  client.addWorkspacePanel({
    id: "ops",
    title: "Repo Ops",
    icon: "Terminal",
    context: "workspace",
    locations: ["workspace", "explorer"],
    Component: OpsPanel,
  });
  client.addCommandCenterItem({
    id: "open-ops",
    title: "Open Repo Ops",
    icon: "Terminal",
    keywords: ["git", "push", "pulumi", "guard"],
    context: "workspace",
    onSelect({ openPanel }: PluginWorkspaceCommandContext) {
      openPanel("ops");
    },
  });
  return () => {};
}
