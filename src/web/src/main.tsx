import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App.tsx";
// Self-hosted fonts (latin subset, only the weights app.css uses): no runtime request to Google.
import "@fontsource/inter/latin-400.css";
import "@fontsource/inter/latin-500.css";
import "@fontsource/inter/latin-600.css";
import "@fontsource/inter/latin-700.css";
import "@fontsource/azeret-mono/latin-500.css";
import "./styles/tokens.css";
import "./styles/app.css";

const root = document.getElementById("root");
if (!root) throw new Error("missing #root element");

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
