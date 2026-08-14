import { useEffect, useRef, useState } from "react";

export interface NavItem {
  id: string;
  label: string;
}

// Dropdown category button for the masthead. Pure local state: click toggles,
// click-outside and Esc close, selecting an item closes and navigates.
export default function NavMenu({
  label,
  items,
  activeId,
  onSelect,
}: {
  label: string;
  items: NavItem[];
  activeId: string;
  onSelect: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const active = items.some((i) => i.id === activeId);
  return (
    <div className="navmenu" ref={ref}>
      <button
        className={active ? "tab active" : "tab"}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        {label} <span className="navmenu-caret">▾</span>
      </button>
      {open && (
        <div className="navmenu-panel" role="menu">
          {items.map((i) => (
            <button
              key={i.id}
              role="menuitem"
              className={i.id === activeId ? "navmenu-item active" : "navmenu-item"}
              onClick={() => {
                setOpen(false);
                onSelect(i.id);
              }}
            >
              {i.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
