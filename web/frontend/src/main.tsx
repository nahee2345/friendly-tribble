import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import { AuthProvider } from "./auth";
import { StrategyDraftProvider } from "./strategyDraft";
import { cleanupLegacyWealthyPwa } from "./pwa";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <StrategyDraftProvider>
          <App />
        </StrategyDraftProvider>
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>
);

// Remove only previously installed Wealthy PWA artifacts. This localhost app
// does not register a service worker or subscribe to external Web Push.
window.addEventListener("load", () => { cleanupLegacyWealthyPwa().catch(() => undefined); });
