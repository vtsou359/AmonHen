/**
 * The Amon Hen mark, redrawn as SVG.
 *
 * Traced from the supplied logo rather than embedding the JPEG: it stays crisp
 * at every size, inherits the gradient from the design tokens, and costs a few
 * hundred bytes instead of a raster round-trip.
 */
export function Logo({ size = 28, className = "" }: { size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 100 100"
      fill="none"
      className={className}
      role="img"
      aria-label="Amon Hen"
    >
      <defs>
        <linearGradient id="amonhen-ramp" x1="50" y1="6" x2="50" y2="94" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#1B1FA0" />
          <stop offset="0.5" stopColor="#0A4A66" />
          <stop offset="1" stopColor="#12855A" />
        </linearGradient>
      </defs>
      {/* Left wing of the A */}
      <path d="M47 6 L28 62 L12 62 L12 78 L34 78 L36 70 L47 20 Z" fill="url(#amonhen-ramp)" />
      {/* Right wing */}
      <path d="M53 6 L72 62 L88 62 L88 78 L66 78 L64 70 L53 20 Z" fill="url(#amonhen-ramp)" />
      {/* The seeing-stone at the centre */}
      <circle cx="50" cy="70" r="13" fill="url(#amonhen-ramp)" />
    </svg>
  );
}
