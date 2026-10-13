import type { PluginWorkspacePanelProps } from "@getpaseo/plugin/client";
import { useRpc } from "@getpaseo/plugin/client";
import { ScrollView, TextInput, useToast } from "@getpaseo/plugin/client/react-native";
import { useMutation } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Pressable, Text, View } from "react-native";
import {
  COMMAND_IDS,
  GUARDS,
  guardRpc,
  runCommandRpc,
  runRawRpc,
  WINDOWS,
  type RunResult,
} from "../shared/ops";

type CommandId = (typeof COMMAND_IDS)[number];
type Guard = (typeof GUARDS)[number];
type Window = (typeof WINDOWS)[number];

const REPO_ACTIONS: readonly { id: CommandId; label: string }[] = [
  { id: "git-status", label: "Status" },
  { id: "git-fetch", label: "Fetch" },
  { id: "git-pull", label: "Pull" },
  { id: "git-push", label: "Push" },
  { id: "preview-my-apps", label: "Preview my-apps" },
  { id: "preview-infra-apps", label: "Preview infra-apps" },
];

const GUARD_LABELS: Record<Guard, string> = {
  "git-push": "git push",
  "pulumi-up": "pulumi up",
  pyinfra: "pyinfra",
  "kubectl-edit": "kubectl edit",
};

export function OpsPanel({ theme, layout, workspaceId }: PluginWorkspacePanelProps) {
  const runCommand = useRpc(runCommandRpc);
  const runRaw = useRpc(runRawRpc);
  const runGuard = useRpc(guardRpc);
  const toast = useToast();

  const [result, setResult] = useState<RunResult | null>(null);
  const [raw, setRaw] = useState("");

  const onError = (error: unknown) => {
    toast.error(error instanceof Error ? error.message : String(error));
  };

  const command = useMutation<RunResult, Error, CommandId>({
    mutationFn: (id) => runCommand({ workspaceId, id }),
    onSuccess: setResult,
    onError,
  });
  const rawRun = useMutation<RunResult, Error, string>({
    mutationFn: (value) => runRaw({ workspaceId, command: value }),
    onSuccess: setResult,
    onError,
  });
  const guard = useMutation<
    RunResult,
    Error,
    { action: "list" | "clear" | "approve"; guard?: Guard; window?: Window }
  >({
    mutationFn: (input) =>
      runGuard({
        workspaceId,
        action: input.action,
        guard: input.guard,
        window: input.window ?? "once",
      }),
    onSuccess: setResult,
    onError,
  });

  const busy = command.isPending || rawRun.isPending || guard.isPending;

  const styles = useMemo(
    () => ({
      screen: { flex: 1, backgroundColor: theme.colors.surface0 },
      content: { padding: layout.compact ? 12 : 16, gap: 16 },
      section: { gap: 8 },
      heading: {
        color: theme.colors.foregroundMuted,
        fontSize: 12,
        letterSpacing: 1,
        textTransform: "uppercase" as const,
      },
      row: { flexDirection: "row" as const, flexWrap: "wrap" as const, gap: 8 },
      button: {
        paddingVertical: 8,
        paddingHorizontal: 12,
        borderRadius: 8,
        borderWidth: 1,
        borderColor: theme.colors.border,
        backgroundColor: theme.colors.surface2,
      },
      buttonText: { color: theme.colors.foreground, fontSize: 13 },
      input: {
        minHeight: 40,
        maxHeight: 120,
        padding: 10,
        borderRadius: 8,
        borderWidth: 1,
        borderColor: theme.colors.border,
        backgroundColor: theme.colors.surface1,
        color: theme.colors.foreground,
      },
      status: { color: theme.colors.foregroundMuted, fontSize: 12 },
      output: {
        padding: 10,
        borderRadius: 8,
        borderWidth: 1,
        borderColor: theme.colors.border,
        backgroundColor: theme.colors.surface1,
      },
      outputText: {
        color: theme.colors.foreground,
        fontFamily: "monospace" as const,
        fontSize: 12,
      },
    }),
    [theme, layout.compact],
  );

  const statusText = busy
    ? "Running..."
    : result
      ? `$ ${result.command}${result.exitCode !== null ? ` (exit ${result.exitCode})` : ""}`
      : "Ready";

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <View style={styles.section}>
        <Text style={styles.heading}>Guard approvals</Text>
        <View style={styles.row}>
          {GUARDS.map((guardId) => (
            <Pressable
              key={guardId}
              accessibilityRole="button"
              disabled={busy}
              style={styles.button}
              onPress={() => guard.mutate({ action: "approve", guard: guardId, window: "once" })}
            >
              <Text style={styles.buttonText}>{GUARD_LABELS[guardId]} once</Text>
            </Pressable>
          ))}
        </View>
        <View style={styles.row}>
          {GUARDS.map((guardId) => (
            <Pressable
              key={`${guardId}-10m`}
              accessibilityRole="button"
              disabled={busy}
              style={styles.button}
              onPress={() => guard.mutate({ action: "approve", guard: guardId, window: "10m" })}
            >
              <Text style={styles.buttonText}>{GUARD_LABELS[guardId]} 10m</Text>
            </Pressable>
          ))}
        </View>
        <View style={styles.row}>
          <Pressable
            accessibilityRole="button"
            disabled={busy}
            style={styles.button}
            onPress={() => guard.mutate({ action: "list" })}
          >
            <Text style={styles.buttonText}>List grants</Text>
          </Pressable>
          <Pressable
            accessibilityRole="button"
            disabled={busy}
            style={styles.button}
            onPress={() => guard.mutate({ action: "clear" })}
          >
            <Text style={styles.buttonText}>Clear grants</Text>
          </Pressable>
        </View>
      </View>

      <View style={styles.section}>
        <Text style={styles.heading}>Repo</Text>
        <View style={styles.row}>
          {REPO_ACTIONS.map((action) => (
            <Pressable
              key={action.id}
              accessibilityRole="button"
              disabled={busy}
              style={styles.button}
              onPress={() => command.mutate(action.id)}
            >
              <Text style={styles.buttonText}>{action.label}</Text>
            </Pressable>
          ))}
        </View>
      </View>

      <View style={styles.section}>
        <Text style={styles.heading}>Command</Text>
        <TextInput
          style={styles.input}
          value={raw}
          onChangeText={setRaw}
          placeholder="git log --oneline -5"
          placeholderTextColor={theme.colors.foregroundMuted}
          autoCapitalize="none"
          autoCorrect={false}
          multiline
        />
        <View style={styles.row}>
          <Pressable
            accessibilityRole="button"
            disabled={busy || raw.trim().length === 0}
            style={styles.button}
            onPress={() => {
              const value = raw.trim();
              if (value) rawRun.mutate(value);
            }}
          >
            <Text style={styles.buttonText}>Run</Text>
          </Pressable>
        </View>
      </View>

      <View style={styles.section}>
        <Text style={styles.status}>{statusText}</Text>
        <View style={styles.output}>
          <Text style={styles.outputText} selectable>
            {result?.output || "(no output)"}
          </Text>
        </View>
      </View>
    </ScrollView>
  );
}
