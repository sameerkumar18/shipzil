/**
 * Brand assets.
 *
 * The mark is the product: three provider sources on the left converging through a
 * single gateway into one interface on the right. It is drawn rather than imported
 * so it inherits `currentColor` in the nav and needs no basePath handling.
 */

export function Mark({ className = 'size-6' }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" fill="none" className={className} aria-hidden="true">
      <defs>
        <linearGradient id="shipzil-mark" x1="0" y1="0" x2="32" y2="32" gradientUnits="userSpaceOnUse">
          <stop stopColor="#4338CA" />
          <stop offset="1" stopColor="#06B6D4" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="7.5" fill="url(#shipzil-mark)" />
      <g stroke="#fff" strokeWidth="2.1" strokeLinecap="round" fill="none">
        <path d="M6 9h4.5" />
        <path d="M6 16h4.5" />
        <path d="M6 23h4.5" />
        <path d="M10.5 9c4.2 0 3.4 7 7 7" />
        <path d="M10.5 23c4.2 0 3.4-7 7-7" />
        <path d="M10.5 16h7" />
        <path d="M17.5 16H26" />
      </g>
    </svg>
  );
}

export function Logo() {
  return (
    <span className="inline-flex items-center gap-2">
      <Mark className="size-6 shrink-0" />
      <span className="text-[15px] font-semibold tracking-tight">shipzil</span>
    </span>
  );
}
