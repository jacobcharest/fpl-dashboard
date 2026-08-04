import { useState } from "react";
import type { PlayerRow, TeamRange, TeamRow } from "../types";
import { ChartCard } from "./ChartCard";
import "./ChartsPanel.css";

interface Props {
  entityType: "player" | "team";
  rows: (PlayerRow | TeamRow)[];
  seasonId: string;
  teamRanges: TeamRange[];
  opponentTeamCodes: number[] | null;
  per90: boolean;
  startsOnly: boolean;
}

let nextId = 1;

export function ChartsPanel(props: Props) {
  const [cardIds, setCardIds] = useState<number[]>([]);

  return (
    <div className="charts-panel">
      <div className="charts-panel-header">
        <h2>Charts</h2>
        <button onClick={() => setCardIds([...cardIds, nextId++])}>+ Add Chart</button>
      </div>
      {cardIds.map((id) => (
        <ChartCard key={id} {...props} onRemove={() => setCardIds(cardIds.filter((c) => c !== id))} />
      ))}
    </div>
  );
}
