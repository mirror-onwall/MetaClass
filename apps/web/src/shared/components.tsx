export function PanelHeading({
  number,
  title,
  aside,
}: {
  number: string;
  title: string;
  aside: string;
}) {
  return (
    <header className="panel-heading">
      <span>{number}</span>
      <h2>{title}</h2>
      <small>{aside}</small>
    </header>
  );
}

export function EmptyState({ symbol, text }: { symbol: string; text: string }) {
  return (
    <div className="empty-state">
      <span>{symbol}</span>
      <p>{text}</p>
    </div>
  );
}
