import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "#0b0e14",
        surface: "#11151d",
        surface2: "#161b26",
        border: "#232a38",
        text: "#e6e9ef",
        muted: "#8b95a7",
        accent: "#4f8cff",
        good: "#2fbf71",
        warn: "#e5a94c",
        bad: "#e5484d",
        proxmox: "#e57000",
      },
    },
  },
  plugins: [],
};

export default config;
