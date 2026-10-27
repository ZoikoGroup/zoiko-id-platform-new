import { expect, test } from "@playwright/test";

import { addVirtualPasskey, registerCompany } from "./helpers";

/**
 * Two people, no identifiers: the owner asks for a residency change, the Administrator they invited sees it waiting on
 * their Overview and approves it with one click, and the owner applies it. Nobody copies a request ID, a grant ID or a
 * transaction-context string. Needs the local stack (cell API and web app) and its development simulators.
 */
test("an Administrator approves the owner's residency change without any identifiers", async ({ browser }) => {
  test.setTimeout(240_000);

  const ownerCtx = await browser.newContext();
  const owner = await ownerCtx.newPage();
  await addVirtualPasskey(ownerCtx, owner);
  await registerCompany(owner);

  // the owner invites an Administrator and hands over the link
  await owner.goto("/admin/team");
  await owner.getByRole("button", { name: "Invite person" }).first().click();
  await owner.getByText("Administrator", { exact: true }).click();
  await owner.getByRole("button", { name: "Create invitation" }).click();
  const link = (await owner.locator("code[aria-label='Passkey setup link']").innerText()).trim();
  expect(link).toContain("workspace="); // the invitee's browser needs the company named in the link
  await owner.getByText("I have shared the link").click();
  await owner.getByRole("button", { name: "Done" }).click();

  // the Administrator, in a separate browser, creates their passkey and joins
  const adminCtx = await browser.newContext();
  const admin = await adminCtx.newPage();
  await addVirtualPasskey(adminCtx, admin);
  await admin.goto(link);
  await admin.getByRole("button", { name: /create passkey/i }).click();
  await admin.waitForURL(/\/(overview|account)/, { timeout: 30_000 });
  // arriving from the setup link counts as joining; the grant that lets them approve is made at that moment
  await expect(admin.getByText(/Welcome to/)).toBeVisible({ timeout: 30_000 });

  // the owner asks for approval from the residency form
  await owner.goto("/admin/residency");
  await owner.getByRole("button", { name: "Change profile" }).click();
  await owner.getByLabel(/Legal mapping reference/).fill("LEGAL-001");
  await owner.getByRole("button", { name: /ask another administrator to approve/i }).click();
  await expect(owner.getByText("Waiting for another administrator")).toBeVisible({ timeout: 20_000 });

  // the Administrator finds it waiting, reads it, and approves
  await admin.goto("/overview");
  await expect(admin.getByText("Waiting for your approval")).toBeVisible({ timeout: 20_000 });
  await expect(admin.getByText(/Set the company's residency to/)).toBeVisible();
  await admin.getByRole("button", { name: "Approve", exact: true }).click();

  // the owner's page notices by itself and lets them apply
  await expect(owner.getByText("Approved by another administrator")).toBeVisible({ timeout: 30_000 });
  await owner.getByRole("button", { name: "Apply profile" }).last().click();
  await expect(owner.getByText("Residency profile updated").first()).toBeVisible({ timeout: 20_000 });

  await ownerCtx.close();
  await adminCtx.close();
});
