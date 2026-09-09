import { lazy, Suspense, type ReactNode } from "react";
import SessionGate from "./SessionGate";

const TearPortalLink = lazy(() => import("../narrative/TearPortalLink"));

/** Keeps the Console tear enhancement behind the existing authenticated branch. */
export default function ConsoleSessionGate({ children }: { children: ReactNode }) {
  return (
    <SessionGate>
      <Suspense fallback={<div className="mb-4 h-24 lg:h-28" aria-hidden="true" />}>
        <TearPortalLink />
      </Suspense>
      {children}
    </SessionGate>
  );
}
