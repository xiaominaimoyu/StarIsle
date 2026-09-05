import type { ReactNode } from "react";

interface EmptyStateProps {
  title: string;
  description: string;
  action?: ReactNode;
}

export default function EmptyState({ title, description, action }: EmptyStateProps) {
  return (
    <div className="card flex flex-col items-center gap-3 px-6 py-12 text-center">
      <span aria-hidden="true" className="text-3xl text-text-dim">✦</span>
      <h3 className="text-lg font-semibold text-text">{title}</h3>
      <p className="max-w-md text-sm text-text-dim">{description}</p>
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}