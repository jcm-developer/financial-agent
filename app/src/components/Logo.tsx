/**
 * The application's mark: a rising equity line inside a rounded square.
 *
 * It is the same drawing as the curve on Resumen —a line that ends in a dot—
 * because that line is what the experiment is for. The square is the navy strip
 * token and the line the sage accent, so it follows the theme like everything
 * else: navy and sage in light, slate and the lighter sage in dark. The favicon
 * (`public/favicon.svg`) is the same drawing with its colours written in, since
 * a favicon cannot read CSS variables: the navy, and the sage one step lighter
 * (#10B981) because at 16 px on navy the accent's #059669 goes muddy.
 *
 * It is decorative next to the wordmark, which carries the name, so it is
 * hidden from screen readers.
 *
 * @param props - Logo props.
 * @param props.className - Classes for the SVG, size included.
 * @return The rendered mark.
 */
export function Logo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" aria-hidden className={className}>
      <rect width="32" height="32" rx="8" fill="var(--color-strip)" />
      <polyline
        points="7,22 12,17 16,19.5 24,10.5"
        fill="none"
        stroke="var(--color-accent)"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="24" cy="10.5" r="2.75" fill="var(--color-accent)" />
    </svg>
  );
}
