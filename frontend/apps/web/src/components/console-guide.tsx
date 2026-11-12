"use client";

import { AssistantWidget } from "@zoiko/assistant-widget";
import { createGuideFetch } from "@zoiko/console-guide";
import { ZoikoMark } from "@zoiko/ui";
import { useMemo } from "react";

import { useRuntime } from "@/lib/runtime";

/** The guide reuses the public assistant's UI with its own wording, so nobody mistakes it for the public assistant. Module-level: a stable reference. */
const COPY = {
  launcher: "Open the console guide",
  launcherClose: "Close the console guide",
  welcomeTitle: "How can I help?",
  welcomeBody: "I explain what your role can do and where things are in the console. I can't see your company's data or change anything.",
  helpHeading: "Pages",
  inputLabel: "Ask about your role or the console",
  thinking: "Checking the role matrix…",
  disabledTitle: "The guide is switched off right now",
  disabledBody: "You can still use the menu.",
  unavailableTitle: "The guide is unavailable",
  unavailableBody: "Nothing was sent. Please use the menu, or try again in a few minutes.",
  forgotten: "This chat was cleared in this window.",
  sourcesHeading: "Source",
};

/** The console guide: a role-aware helper for every signed-in person (the five customer roles). It is guide-only by design:
 *  - it answers from two READ-ONLY catalogues the console already uses for its menus (the published role matrix and the person's own
 *    roles), through the same typed client and session as every other screen;
 *  - the question is answered in this browser by a deterministic engine: no model, no server call for the question, nothing stored;
 *  - it cannot read company data and cannot change anything. The cell still decides every request.
 * The widget UI is the public assistant's, reused with a local `fetchImpl`, so focus handling, escaped rendering and accessibility are the
 * same code that was tested there. */
export function ConsoleGuide() {
  const { api } = useRuntime();
  const guideFetch = useMemo(
    () =>
      createGuideFetch({
        loadMatrix: () => api.tenant.roleMatrix(),
        loadPermissions: () => api.tenant.myPermissions(),
      }),
    [api],
  );
  return (
    <AssistantWidget
      apiBase="/guide"
      fetchImpl={guideFetch}
      copy={COPY}
      logo={<ZoikoMark />}
      audiences={[]}
      fallbackLinks={[{ label: "My account", href: "/account" }]}
    />
  );
}
