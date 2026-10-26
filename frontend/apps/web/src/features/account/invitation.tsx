"use client";

import { ApiError } from "@zoiko/api-client";
import { Banner, Button } from "@zoiko/ui";
import { PartyPopper, UserPlus } from "lucide-react";
import { useEffect, useRef } from "react";

import { ApiErrorView, useCellMutation, useCellQuery } from "@/lib/cell";
import { useRuntime } from "@/lib/runtime";

/** A pending tenant invitation for the signed-in person (OVERLAY-001 §4).
 *
 * Joining is the person's own act. Arriving straight from the passkey setup link
 * (`/account?welcome=1`) counts as that act, so it joins at once; otherwise a banner offers it. */
export function PendingInvitation({ autoJoin }: { autoJoin: boolean }) {
  const tenant = useCellQuery(["tenant", "me"], async (api) => {
    try {
      return await api.tenant.me();
    } catch (e) {
      if (e instanceof ApiError && (e.status === 404 || e.status === 403)) return null; // no tenant record
      throw e;
    }
  });
  const join = useCellMutation<void, unknown>((api) => api.tenant.accept(), {
    invalidate: [["tenant", "me"]],
    success: "You joined the tenant",
  });
  const invited = tenant.data?.my_roles.find((r) => r.status === "INVITED");
  const tried = useRef(false);
  // A state-changing call carries the session's CSRF token, which arrives with the session: joining before it is
  // known is refused ("could not be verified"), so wait for it.
  const { session } = useRuntime();
  const ready = session?.authenticated === true;

  useEffect(() => {
    if (autoJoin && invited && ready && !tried.current) {
      tried.current = true;
      join.mutate();
    }
  }, [autoJoin, invited, ready, join]);

  if (join.isSuccess) {
    return (
      <Banner tone="ok" icon={PartyPopper} title={`Welcome to ${tenant.data?.display_name ?? "the tenant"}`}>
        Your passkey is set up and you have joined. The person who invited you can now see that you are in.
      </Banner>
    );
  }
  if (!invited) return null;
  return (
    <Banner tone="info" icon={UserPlus} title={`You're invited to ${tenant.data?.display_name ?? "this tenant"} as ${invited.role.toLowerCase()}`}>
      <div className="mt-2 flex flex-wrap items-center gap-3">
        <Button size="sm" pending={join.isPending} onPress={() => join.mutate()}>
          Join
        </Button>
        {join.error ? <ApiErrorView error={join.error} /> : null}
      </div>
    </Banner>
  );
}
