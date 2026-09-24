import { useId } from "react";

interface SealMonogramProps {
  readonly className?: string;
  readonly title?: string;
  /** `seal` is the full wax die, `cipher` the bare letters, `compact` survives favicon sizes. */
  readonly variant?: "seal" | "cipher" | "compact";
}

const SCALLOP_ANGLES = [0, 30, 60, 90, 120, 150, 180, 210, 240, 270, 300, 330] as const;
const LOBE_RING = 41.8;
const LOBE_RADIUS = 5.4;
const RING_RADIUS = 34.5;

/** Measured extents of the three letter paths: x 23..76.5, y 27.5..67. */
const CIPHER_CX = 49.75;
const CIPHER_CY = 47.25;
/** The block's half-diagonal is 33.25; 0.84 keeps stroked corners inside the ring. */
const CIPHER_FIT = 0.84;

function fitCipher(scale: number) {
  return `translate(${(50 - CIPHER_CX * scale).toFixed(2)} ${(50 - CIPHER_CY * scale).toFixed(2)}) scale(${scale})`;
}

function scallop(cx: number, cy: number, radius: number, angle: number) {
  const rad = (angle * Math.PI) / 180;
  return {
    cx: +(cx + radius * Math.cos(rad)).toFixed(2),
    cy: +(cy + radius * Math.sin(rad)).toFixed(2),
  };
}

/**
 * Interlaced SMY cipher: three single-stroke letters sharing one band, woven so
 * S passes over M low while Y passes over M high.
 */
export default function SealMonogramSvg({
  className,
  title,
  variant = "seal",
}: SealMonogramProps) {
  // More than one seal can share a document (header, footer, drawer), so paint
  // ids have to be instance-scoped rather than one literal.
  const uid = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const waxId = `seal-monogram-wax-${uid}`;
  const shadeId = `seal-monogram-shade-${uid}`;
  const goldId = `seal-monogram-gold-${uid}`;
  const falloffId = `seal-monogram-falloff-${uid}`;
  const gold = `url(#${goldId})`;
  const shade = `url(#${shadeId})`;
  const compact = variant === "compact";

  const letters = (
    <>
      {/* S — sweeps left of centre and crosses the M stem twice. */}
      <path d="M 40.5 30 C 30.5 27.5 23 34 27.5 41 C 32 48 41 49 43 55 C 45 62.5 36.5 67 27 62.5" />
      {/* M — the shared upright, drawn between S and Y. */}
      <path d="M 40.5 66.5 L 40.5 30 L 50.5 48.5 L 60.5 30 L 60.5 66.5" />
      {/* Y — arms open across the M's right leg. */}
      <path d="M 56.5 30 L 66.5 48 L 76.5 30 M 66.5 48 L 66.5 66.5" />
    </>
  );
  const sOverM = "M 43 55 C 45 62.5 36.5 67 27 62.5";

  const cipher = (
    <g
      transform={fitCipher(compact ? 0.88 : CIPHER_FIT)}
      fill="none"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {/* The dark pass sits low-right so the letters read as pressed in, not outlined. */}
      <g stroke={shade} strokeWidth={compact ? 8.6 : 5.2} transform="translate(0.9 1.2)">{letters}</g>
      <g stroke={gold} strokeWidth={compact ? 5.4 : 3.1}>{letters}</g>
      {/* Re-stroke the S terminal so the weave alternates over the M stem. */}
      <path d={sOverM} stroke={shade} strokeWidth={compact ? 8.6 : 5.2} transform="translate(0.9 1.2)" />
      <path d={sOverM} stroke={gold} strokeWidth={compact ? 5.4 : 3.1} />
    </g>
  );

  const gradients = (
    <defs>
      <linearGradient id={goldId} x1="24%" y1="12%" x2="78%" y2="94%">
        <stop offset="0" stopColor="#e3c795" />
        <stop offset="0.4" stopColor="#b98f52" />
        <stop offset="1" stopColor="#6f4f22" />
      </linearGradient>
      <linearGradient id={shadeId} x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stopColor="#150604" stopOpacity="0.92" />
        <stop offset="1" stopColor="#3a1510" stopOpacity="0.72" />
      </linearGradient>
    </defs>
  );

  if (variant === "cipher") {
    return (
      <svg
        className={className}
        viewBox="25 31 50 38"
        role={title ? "img" : undefined}
        aria-label={title}
        aria-hidden={title ? undefined : "true"}
        focusable="false"
      >
        {gradients}
        {cipher}
      </svg>
    );
  }

  return (
    <svg
      className={className}
      viewBox="0 0 100 100"
      role={title ? "img" : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : "true"}
      focusable="false"
    >
      <defs>
        {/* Tones sampled from the photographed die: near-black oxblood, not brick red.
            userSpaceOnUse keeps the scallop lobes on one light source. */}
        <radialGradient id={waxId} gradientUnits="userSpaceOnUse" cx="36" cy="31" r="58">
          <stop offset="0" stopColor="#6d3226" />
          <stop offset="0.4" stopColor="#4a1c14" />
          <stop offset="0.8" stopColor="#2a0b07" />
          <stop offset="1" stopColor="#150403" />
        </radialGradient>
        {/* Opposing falloff so the rim sinks into the envelope instead of cutting it. */}
        <radialGradient id={falloffId} gradientUnits="userSpaceOnUse" cx="63" cy="69" r="54">
          <stop offset="0" stopColor="#0d0302" stopOpacity="0.5" />
          <stop offset="0.55" stopColor="#0d0302" stopOpacity="0.15" />
          <stop offset="1" stopColor="#0d0302" stopOpacity="0" />
        </radialGradient>
        {gradients}
      </defs>

      <g fill={`url(#${waxId})`}>
        {SCALLOP_ANGLES.map((angle) => {
          const point = scallop(50, 50, LOBE_RING, angle);
          return <circle key={angle} cx={point.cx} cy={point.cy} r={LOBE_RADIUS} />;
        })}
        <circle cx="50" cy="50" r="43" />
      </g>
      <circle cx="50" cy="50" r="43" fill={`url(#${falloffId})`} />
      <ellipse
        cx="37"
        cy="32"
        rx="24"
        ry="16"
        fill="#e8b184"
        opacity="0.1"
        transform="rotate(-24 37 32)"
      />

      {!compact && (
        <>
          <circle cx="50" cy="50" r={RING_RADIUS + 3} fill="none" stroke="#120403" strokeOpacity="0.5" strokeWidth="1.5" />
          <circle cx="50" cy="50" r={RING_RADIUS} fill="none" stroke={gold} strokeOpacity="0.4" strokeWidth="1" />
          <g fill={gold} opacity="0.5">
            <path d="M50 18.6 51.3 21.9 54.7 22.1 52 24.3 52.9 27.6 50 25.7 47.1 27.6 48 24.3 45.3 22.1 48.7 21.9Z" />
            <circle cx="29" cy="50" r="1.1" />
            <circle cx="71" cy="50" r="1.1" />
            <circle cx="50" cy="78" r="1.1" />
          </g>
        </>
      )}

      {cipher}
    </svg>
  );
}
