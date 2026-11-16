"use client";

import { useMe } from "../authority/shared";
import { WorkflowVersionPage } from "./workflows";

/** Supplies who is signed in, so the page can say "you wrote this, someone else approves it" before the platform has to refuse. */
export function WorkflowVersionRoute({ id, version }: { id: string; version: string }) {
  return <WorkflowVersionPage id={id} version={version} me={useMe()} />;
}
