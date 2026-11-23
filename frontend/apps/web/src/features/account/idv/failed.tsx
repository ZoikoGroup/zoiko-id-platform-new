"use client";

import { ApiError } from "@zoiko/api-client";
import { Banner } from "@zoiko/ui";

import { ApiErrorView } from "@/lib/cell";

import { applicantText } from "./logic";

/** A refusal in the applicant's words; anything the page cannot explain falls back to the uniform error view. */
export function Failed({ error }: { error: unknown }) {
  if (error instanceof ApiError && [403, 404, 409, 422, 429, 503].includes(error.status)) {
    return (
      <Banner tone="danger" title="This could not be done">
        {applicantText(String(error.code ?? ""))}
      </Banner>
    );
  }
  return <ApiErrorView error={error} />;
}
