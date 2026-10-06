/**
 * Brand colors for Tailwind (CDN build). Loaded right after the Tailwind script.
 * Usage in HTML: bg-navy, text-brand, border-glow, text-muted, bg-velocity ...
 */
tailwind.config = {
  theme: {
    extend: {
      colors: {
        navy: "#0F172A",     // Slate Navy: background / primary text
        brand: "#2563EB",    // Electric Blue: primary brand
        velocity: "#06B6D4", // Velocity Cyan: accent
        glow: "#38BDF8",     // Sky Glow: highlights
        muted: "#64748B",    // Muted Slate: secondary text
      },
    },
  },
};