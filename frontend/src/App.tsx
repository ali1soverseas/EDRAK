import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { ShellLayout } from "./components/shell/AppShell";
import { I18nProvider } from "./i18n";
import { CompanyProfile } from "./pages/CompanyProfile";
import { Dashboard } from "./pages/Dashboard";
import { Home } from "./pages/Home";
import { Sources } from "./pages/Sources";
import { Alerts, Decisions, EvidenceExplorer, Monitors, Reports, Settings, Watchlist } from "./pages/Workspace";
import { LiveRun } from "./pages/LiveRun";
import { NewAnalysis } from "./pages/NewAnalysis";
import { Onboarding } from "./pages/Onboarding";
import { PlanReview } from "./pages/PlanReview";
import { Report } from "./pages/Report";
import { SignIn } from "./pages/SignIn";
import { AuthProvider } from "./state/auth";

export function App() {
  return (
    <I18nProvider>
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/sign-in" element={<SignIn />} />
            <Route path="/onboarding" element={<Onboarding />} />
            <Route element={<ShellLayout />}>
              <Route index element={<Home />} />
              <Route path="analyses" element={<Dashboard />} />
              <Route path="sources" element={<Sources />} />
              <Route path="reports" element={<Reports />} />
              <Route path="monitors" element={<Monitors />} />
              <Route path="alerts" element={<Alerts />} />
              <Route path="watchlist" element={<Watchlist />} />
              <Route path="watchlist/:id" element={<Watchlist />} />
              <Route path="evidence" element={<EvidenceExplorer />} />
              <Route path="evidence/:briefId" element={<EvidenceExplorer />} />
              <Route path="decisions" element={<Decisions />} />
              <Route path="settings" element={<Settings />} />
              <Route path="company" element={<CompanyProfile />} />
              <Route path="analyses/new" element={<NewAnalysis />} />
              <Route path="analyses/:id/plan" element={<PlanReview />} />
              <Route path="analyses/:id/run" element={<LiveRun />} />
              <Route path="briefs/:id" element={<Report />} />
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </I18nProvider>
  );
}
