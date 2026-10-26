"use client";

import { createContext, useContext, type ReactNode } from "react";

import { DEFAULT_LOCALE } from "../utils/locale";

const LocaleContext = createContext<string>(DEFAULT_LOCALE);

/** The locale dates, times and amounts are shown in (UX-31). Decided on the server, so it is the same in the browser. */
export function LocaleProvider({ locale, children }: { locale: string; children: ReactNode }) {
  return <LocaleContext.Provider value={locale}>{children}</LocaleContext.Provider>;
}

export function useLocale(): string {
  return useContext(LocaleContext);
}
