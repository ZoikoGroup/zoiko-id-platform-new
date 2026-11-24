export type SessionTone = "neutral" | "info" | "ok" | "warn" | "danger";

const STATES: Record<string, { label: string; tone: SessionTone }> = {
  created: { label: "Being set up", tone: "neutral" },
  ready: { label: "Waiting for the customer", tone: "info" },
  opened: { label: "Customer opened the link", tone: "info" },
  capture_started: { label: "Customer is taking photos", tone: "info" },
  submitted: { label: "Submitted", tone: "info" },
  processing: { label: "Being checked", tone: "info" },
  action_required: { label: "Customer must retry", tone: "warn" },
  manual_review: { label: "With a reviewer", tone: "warn" },
  completed: { label: "Finished", tone: "ok" },
  inconclusive: { label: "No answer", tone: "warn" },
  expired: { label: "Ran out of time", tone: "neutral" },
  canceled: { label: "Canceled", tone: "neutral" },
};

/** A session's state in the words a business person would use. An unknown state is shown as it is rather than hidden. */
export function sessionState(status: string): { label: string; tone: SessionTone } {
  return STATES[status] ?? { label: status, tone: "neutral" };
}
