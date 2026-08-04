import { useState } from "react";
import "./EntityPicker.css";

interface Entity {
  code: number;
  name: string;
}

interface Props {
  entities: Entity[];
  selected: number[];
  onChange: (codes: number[]) => void;
  maxSelected?: number;
}

export function EntityPicker({ entities, selected, onChange, maxSelected }: Props) {
  const [search, setSearch] = useState("");
  const filtered = entities.filter((e) => e.name.toLowerCase().includes(search.toLowerCase()));

  const toggle = (code: number) => {
    if (selected.includes(code)) {
      onChange(selected.filter((c) => c !== code));
    } else {
      if (maxSelected && selected.length >= maxSelected) return;
      onChange([...selected, code]);
    }
  };

  return (
    <div className="entity-picker">
      <input
        className="entity-picker-search"
        type="text"
        placeholder="Search…"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />
      <div className="entity-picker-list">
        {filtered.slice(0, 200).map((e) => (
          <label key={e.code} className="entity-picker-row">
            <input type="checkbox" checked={selected.includes(e.code)} onChange={() => toggle(e.code)} />
            {e.name}
          </label>
        ))}
        {filtered.length === 0 && <div className="entity-picker-empty">No matches</div>}
      </div>
      <div className="entity-picker-count">
        {selected.length} selected{maxSelected ? ` (max ${maxSelected})` : ""}
      </div>
    </div>
  );
}
