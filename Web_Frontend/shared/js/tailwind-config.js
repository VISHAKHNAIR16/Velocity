/**
 * Brand colors for Tailwind (CDN build). Loaded right after the Tailwind script.
 * Values mirror the CSS variables in shared/css/theme.css so utility classes
 * like `bg-page`, `text-navy`, `border-line` or `bg-danger-soft` all work.
 */
tailwind.config = {
  theme: {
    extend: {
      colors: {
        // Brand
        navy: "#0F172A",     // Slate Navy: background / primary text
        brand: "#2563EB",    // Electric Blue: primary brand
        velocity: "#06B6D4", // Velocity Cyan: accent
        glow: "#38BDF8",     // Sky Glow: highlights
        muted: "#64748B",    // Muted Slate: secondary text

        // Surfaces (from theme.css :root)
        page: "#F4F8FC",
        surface: "#FFFFFF",
        tint: "#EEF4FB",
        hover: "#E8F3FF",
        line: "#E1E8F0",
        "line-soft": "#EDF1F6",
        "brand-soft": "#E1ECFF",
        "cyan-soft": "#DAF5F9",

        // Status
        ok: "#047857",
        "ok-soft": "#DCFCE7",
        warn: "#B45309",
        "warn-soft": "#FEF3C7",
        danger: "#B91C1C",
        "danger-soft": "#FEE2E2",
      },
    },
  },
};