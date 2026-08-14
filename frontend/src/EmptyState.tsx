// One-liner + guidance instead of a blank panel when a view has no data yet.
export default function EmptyState({ title, hint }: { title: string; hint: string }) {
  return (
    <div className="panel empty-state">
      <p><strong>{title}</strong></p>
      <p className="hint">{hint}</p>
    </div>
  );
}
