/** Liquid-wax overlays; the intact seal itself comes from the original project asset. */
export default function WaxSealSvg() {
  return (
    <svg
      className="narrative-entry__seal-svg"
      viewBox="0 0 220 260"
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <radialGradient id="narrative-wax-heat" cx="50%" cy="48%" r="50%">
          <stop offset="0" stopColor="#ffd6a0" stopOpacity="0.45" />
          <stop offset="0.5" stopColor="#b55d34" stopOpacity="0.2" />
          <stop offset="1" stopColor="#5c241b" stopOpacity="0" />
        </radialGradient>
        <linearGradient id="narrative-wax-liquid" x1="24%" y1="8%" x2="74%" y2="100%">
          <stop offset="0" stopColor="#87503c" />
          <stop offset="0.24" stopColor="#653025" />
          <stop offset="0.7" stopColor="#3b1815" />
          <stop offset="1" stopColor="#1c0909" />
        </linearGradient>
        <linearGradient id="narrative-wax-highlight" x1="20%" y1="0" x2="80%" y2="100%">
          <stop offset="0" stopColor="#f2c295" stopOpacity="0.54" />
          <stop offset="0.42" stopColor="#a85b43" stopOpacity="0.24" />
          <stop offset="1" stopColor="#6f271e" stopOpacity="0" />
        </linearGradient>
        <filter id="narrative-wax-wobble" x="-24%" y="-20%" width="148%" height="158%">
          <feTurbulence
            type="fractalNoise"
            baseFrequency="0.018 0.035"
            numOctaves="1"
            seed="41"
            result="waxNoise"
          />
          <feDisplacementMap
            in="SourceGraphic"
            in2="waxNoise"
            scale="4.5"
            xChannelSelector="R"
            yChannelSelector="G"
          />
          <feDropShadow dx="0" dy="8" stdDeviation="7" floodColor="#050109" floodOpacity="0.72" />
        </filter>
        <filter id="narrative-wax-soft-glow" x="-40%" y="-40%" width="180%" height="180%">
          <feGaussianBlur stdDeviation="7" />
        </filter>
      </defs>

      <g className="narrative-entry__wax-layer narrative-entry__wax-layer--heat" data-layer="heat">
        <ellipse
          cx="110"
          cy="94"
          rx="76"
          ry="66"
          fill="url(#narrative-wax-heat)"
          filter="url(#narrative-wax-soft-glow)"
        />
        <ellipse
          cx="110"
          cy="96"
          rx="58"
          ry="51"
          fill="none"
          stroke="#e5a56e"
          strokeOpacity="0.42"
          strokeWidth="2"
        />
      </g>

      <g
        className="narrative-entry__wax-layer narrative-entry__wax-layer--pool"
        data-layer="pool"
        filter="url(#narrative-wax-wobble)"
      >
        <path
          d="M48 105c8-17 24-27 43-29 13-12 34-10 45 1 19 1 33 12 38 27 5 17-8 31-25 35-12 11-31 13-45 6-17 5-36-1-44-13-13-4-18-15-12-27Z"
          fill="url(#narrative-wax-liquid)"
        />
        <path
          d="M68 96c13-12 27-16 42-15 11-5 25-2 34 6-22-5-47 1-62 16-7 7-11 15-12 25-10-8-11-21-2-32Z"
          fill="url(#narrative-wax-highlight)"
          opacity="0.38"
        />
      </g>

      <g
        className="narrative-entry__wax-layer narrative-entry__wax-layer--highlights"
        data-layer="highlights"
        fill="none"
        stroke="url(#narrative-wax-highlight)"
        strokeLinecap="round"
      >
        <path d="M65 103c7-12 18-19 31-22" strokeWidth="4.5" />
        <path d="M104 132c9 4 13 12 11 22" strokeWidth="3" />
        <path d="M138 127c6 3 9 8 9 14" strokeWidth="2.2" />
      </g>
    </svg>
  );
}
