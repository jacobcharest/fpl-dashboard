import { useEffect, useRef, useState } from "react";
import { POSITIONS } from "../types";
import "./PositionFilter.css";

interface Props {
  selected: string[] | null; // null = all positions (no filter)
  onChange: (positions: string[] | null) => void;
}

export function PositionFilter({ selected, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const active: string[] = selected ?? [...POSITIONS];

  useEffect(() => {
    const onClickOutside = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  const toggle = (pos: string) => {
    const next = active.includes(pos) ? active.filter((p) => p !== pos) : [...active, pos];
    onChange(next.length === POSITIONS.length ? null : next);
  };

  const label = selected === null ? "All" : selected.length === 0 ? "None" : selected.join(", ");

  return (
    <div className="position-filter" ref={ref}>
      <button className="position-filter-toggle" onClick={() => setOpen((o) => !o)}>
        {label} ▾
      </button>
      {open && (
        <div className="position-filter-menu">
          {POSITIONS.map((pos) => (
            <label key={pos} className="position-filter-option">
              <input type="checkbox" checked={active.includes(pos)} onChange={() => toggle(pos)} />
              {pos}
            </label>
          ))}
        </div>
      )}
    </div>
  );
}
