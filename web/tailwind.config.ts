import type { Config } from "tailwindcss";

// Every colour is a CSS variable holding "R G B" (see globals.css), so opacity modifiers such as
// bg-good/15 keep working and the whole app re-themes by flipping data-theme on <html>.
const c = (name: string) => `rgb(var(--c-${name}) / <alpha-value>)`;

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  darkMode: ["selector", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        canvas: c("canvas"),
        surface: c("surface"),
        surface2: c("surface2"),
        border: c("border"),
        text: c("text"),
        muted: c("muted"),
        accent: c("accent"),
        good: c("good"),
        warn: c("warn"),
        bad: c("bad"),
        proxmox: c("proxmox"),
        ink: c("ink"),
      },
    },
  },
  plugins: [],
};

export default config;
