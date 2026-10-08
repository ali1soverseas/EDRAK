import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
// The design: fonts and tokens first, exactly as handed over, then the app's additions.
import "./styles/fonts.css";
import "./styles/edrak.css";
import "./styles/app.css";
import "./styles/signin.css";
import "./styles/company.css";
import "./styles/dashboard.css";
import "./styles/analysis.css";
import "./styles/run.css";
import "./styles/brief.css";
import "./styles/responsive.css";

const root = document.getElementById("root");

if (!root) {
  throw new Error("Root element #root is missing.");
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
