import { useEffect, useState } from "react";

// Theme hook — localStorage me save karta hai, default OS preference se lega.
// Analyst log session lambi hoti hai, manual toggle dena helpful hai.

export type Theme = "light" | "dark";
const KEY = "esg-theme";

function preferred(): Theme {
  const stored = localStorage.getItem(KEY) as Theme | null;
  if (stored === "light" || stored === "dark") return stored;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(preferred);
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem(KEY, theme);
  }, [theme]);
  return [theme, () => setTheme(t => (t === "dark" ? "light" : "dark"))];
}
