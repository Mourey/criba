import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Specimen } from "./Specimen.tsx";
// Same self-hosted fonts and stylesheets as main.tsx, so the specimen renders in the app's own world.
import "@fontsource/inter/latin-400.css";
import "@fontsource/inter/latin-500.css";
import "@fontsource/inter/latin-600.css";
import "@fontsource/inter/latin-700.css";
import "@fontsource/azeret-mono/latin-500.css";
import "../../styles/tokens.css";
import "../../styles/app.css";

const root = document.getElementById("root");
if (!root) throw new Error("missing #root element");

createRoot(root).render(
  <StrictMode>
    <Specimen />
  </StrictMode>,
);
