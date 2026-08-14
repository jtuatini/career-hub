import { useEffect, useRef, useState } from "react";
import type { ReactNode, MouseEvent } from "react";

/** Two-step delete confirmation without window.confirm (Chrome can suppress
 * native dialogs silently). First click arms for 4s; second click confirms. */
export default function ConfirmButton({
  className = "ghost",
  confirmText = "Confirm?",
  onConfirm,
  onError,
  ariaLabel,
  children,
}: {
  className?: string;
  confirmText?: string;
  onConfirm: () => void | Promise<void>;
  onError?: (e: Error) => void;
  ariaLabel?: string;
  children: ReactNode;
}) {
  const [armed, setArmed] = useState(false);
  const timer = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (timer.current) window.clearTimeout(timer.current);
    },
    [],
  );
  const click = async (e: MouseEvent) => {
    e.stopPropagation();
    if (!armed) {
      setArmed(true);
      timer.current = window.setTimeout(() => setArmed(false), 4000);
      return;
    }
    if (timer.current) window.clearTimeout(timer.current);
    setArmed(false);
    try {
      await onConfirm();
    } catch (err) {
      onError?.(err as Error);
    }
  };
  return (
    <button className={armed ? "confirm-armed" : className} aria-label={ariaLabel} onClick={click}>
      {armed ? confirmText : children}
    </button>
  );
}
