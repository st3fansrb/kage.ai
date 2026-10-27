"use client";

import { useEffect, useRef } from "react";
import { t } from "@/lib/i18n";

// Foaie laterală pe <dialog> nativ: focus prins înăuntru, Esc închide, focusul revine
// pe butonul care a deschis-o.
export function Sheet({
  open,
  onClose,
  title,
  meta,
  children,
  footer,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  meta?: React.ReactNode;
  children: React.ReactNode;
  footer?: React.ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      className="sheet"
      aria-label={title}
      onClose={onClose}
      onClick={(e) => {
        if (e.target === ref.current) onClose(); // clic pe fundal
      }}
    >
      {open && (
        <>
          <div className="sheet-head">
            <h2>{title}</h2>
            {meta}
            <button className="icon-btn x" onClick={onClose} aria-label={`${t("Închide", "Close")} ${title.toLowerCase()}`}>
              <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
                <path d="M3.5 3.5l9 9M12.5 3.5l-9 9" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
              </svg>
            </button>
          </div>
          {children}
          {footer && <div className="sheet-foot">{footer}</div>}
        </>
      )}
    </dialog>
  );
}
