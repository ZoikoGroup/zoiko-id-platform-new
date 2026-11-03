/** The legal documents people are shown at sign-up. Legal and Privacy own the wording; engineering owns only what the pages say the platform
 * does. Change a notice's text and you change its version, here and in the cell's ZID_TENANT_SIGNUP_NOTICE_VERSION (Settings default), together:
 * the cell refuses a sign-up that acknowledged any other version. */
export const PRIVACY_NOTICE_VERSION = "2026-11-03";

/** The Terms of Service are not published yet (Legal has not supplied them). Until they are, the sign-up page shows no link to them and
 * `/legal/terms` answers "not found", rather than a link to a page with nothing to read. Publishing is: put the text in the terms page,
 * set `published` to true and give it a `version`; then the link appears. */
export const TERMS: { published: boolean; version: string | null } = { published: false, version: null };
