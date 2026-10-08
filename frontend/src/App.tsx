import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { ShellLayout } from "./components/shell/AppShell";
import { I18nProvider } from "./i18n";
import { Company } from "./pages/Company";
import { Dashboard } from "./pages/Dashboard";
import { LiveRun } from "./pages/LiveRun";
import { NewAnalysis } from "./pages/NewAnalysis";
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
            <Route element={<ShellLayout />}>
              <Route index element={<Dashboard />} />
              <Route path="company" element={<Company />} />
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
