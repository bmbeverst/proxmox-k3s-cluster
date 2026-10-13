import type { PluginServerContext } from "@getpaseo/plugin/server";
import { runCommand, runGuard, runRaw } from "./server/ops";
import { guardRpc, runCommandRpc, runRawRpc } from "./shared/ops";

export default function contribute(server: PluginServerContext) {
  server.handle(runCommandRpc, runCommand);
  server.handle(runRawRpc, runRaw);
  server.handle(guardRpc, runGuard);
  return () => {};
}
