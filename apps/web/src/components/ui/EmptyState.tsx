import type { ReactNode } from "react";

interface EmptyStateProps {
  title: string;
  description: string;
  action?: ReactNode;
}

export default function EmptyState({ title, description, action }: EmptyStateProps) {
  return (
    <div className="card flex flex-col items-start gap-3 rounded-sm border-l-2 border-l-accent-warm px-6 py-12 text-left">
      <span aria-hidden="true" className="text-3xl text-text-dim">✦</span>
      <h3 className="text-lg font-semibold text-text">{title}</h3>
      <p className="max-w-md text-sm text-text-dim">{description}</p>
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}
