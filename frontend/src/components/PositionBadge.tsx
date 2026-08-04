import "./PositionBadge.css";

export function PositionBadge({ position }: { position: string }) {
  return <span className={`position-badge position-${position}`}>{position}</span>;
}
