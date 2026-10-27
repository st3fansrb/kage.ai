// Limba interfeței, fixată la build: NEXT_PUBLIC_KAGE_LANG=en pentru engleză, altfel română.
export const LANG: "ro" | "en" = process.env.NEXT_PUBLIC_KAGE_LANG === "en" ? "en" : "ro";
export const LOCALE = LANG === "en" ? "en-GB" : "ro-RO";

// Textul în limba curentă; fiecare apel ține ambele variante lângă locul unde se folosesc.
export function t(ro: string, en: string): string {
  return LANG === "en" ? en : ro;
}
