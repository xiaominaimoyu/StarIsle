/** Purely decorative, programmatic wax artwork for the /enter route. */
export default function WaxSealSvg() {
  return (
    <svg
      className="narrative-entry__seal-svg"
      viewBox="0 0 180 180"
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <filter
          id="narrative-wax-filter"
          x="20"
          y="18"
          width="140"
          height="145"
          filterUnits="userSpaceOnUse"
          primitiveUnits="userSpaceOnUse"
        >
          <feTurbulence
            type="fractalNoise"
            baseFrequency="0.018"
            numOctaves="2"
            seed="37"
            result="grain"
          />
          <feDisplacementMap
            in="SourceGraphic"
            in2="grain"
            scale="3"
            xChannelSelector="R"
            yChannelSelector="G"
          />
        </filter>
      </defs>

      <g className="narrative-entry__wax-deform" filter="url(#narrative-wax-filter)">
        <g className="narrative-entry__wax-layer narrative-entry__wax-layer--base" data-layer="base">
          <path d="M90 24c19 0 29 8 43 20 13 11 18 26 16 43-1 16-9 27-19 39-10 11-23 18-40 18-18 0-32-6-43-18-10-12-17-25-16-41 0-17 7-30 19-42 12-12 23-19 40-19Z" />
        </g>
        <g className="narrative-entry__wax-layer narrative-entry__wax-layer--rim" data-layer="rim">
          <circle cx="90" cy="84" r="47" />
          <circle cx="90" cy="84" r="40" />
        </g>
        <g className="narrative-entry__wax-layer narrative-entry__wax-layer--imprint" data-layer="imprint">
          <path d="M90 48 100 66 120 73 105 83l3 22-18-11-18 11 3-22-15-10 20-7 10-18Z" />
          <path d="M84 67h12v29H84zM72 78h36v10H72z" opacity="0.72" />
        </g>
      </g>

      <g className="narrative-entry__wax-layer narrative-entry__wax-layer--residue" data-layer="residue">
        <path d="M48 124c15 5 27 10 42 10 14 0 28-4 43-11l-7 20c-12 7-24 11-37 10-14 0-25-4-35-10l-6-19Z" />
        <path d="M70 139c2 6 3 11 1 16-1 4-5 5-8 2-4-4-1-12 7-18Zm41-2c7 4 10 12 6 17-3 4-7 2-8-2-2-5-1-10 2-15Z" />
      </g>

      <g className="narrative-entry__wax-layer narrative-entry__wax-layer--magic" data-layer="magic">
        <circle cx="46" cy="62" r="2.3" />
        <circle cx="132" cy="56" r="1.8" />
        <circle cx="142" cy="91" r="2.1" />
        <circle cx="38" cy="99" r="1.6" />
        <circle cx="62" cy="38" r="1.5" />
        <circle cx="118" cy="126" r="1.7" />
        <circle cx="53" cy="129" r="1.3" />
        <circle cx="126" cy="35" r="1.2" />
      </g>
    </svg>
  );
}
