import type { ReactNode } from "react";

/**
 * Free text that someone else wrote (an issuer's name, a title, a person's name) shown inside a sentence or a table cell.
 *
 * Without isolation, a right-to-left name next to left-to-right text can reorder the characters around it (punctuation and numbers
 * jump to the wrong side), and a name can appear to end somewhere it does not. `<bdi dir="auto">` makes the browser choose the
 * direction from the text itself and keeps it from affecting its neighbours. It changes nothing for Latin text.
 *
 * Use it for names and titles, never for identifiers or codes: those are always left to right and stay in a monospace span.
 */
export function UserText({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <bdi dir="auto" className={className}>
      {children}
    </bdi>
  );
}

/** The direction a piece of text reads in, from its first strongly directional letter (what `dir="auto"` does), for places that
 * cannot use markup, such as a chart label or a document title. Neutral text (digits, punctuation) is left to right. */
export function textDirection(text: string): "ltr" | "rtl" {
  for (const ch of text) {
    // Hebrew, Arabic, Syriac, Thaana, NKo, Samaritan, Mandaic and the Arabic presentation forms
    if (/[֐-ࣿיִ-﷿ﹰ-﻿]/u.test(ch)) return "rtl";
    if (/\p{L}/u.test(ch)) return "ltr";
  }
  return "ltr";
}
