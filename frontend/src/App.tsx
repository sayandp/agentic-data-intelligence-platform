import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import SourcesPage from "./pages/SourcesPage";
import ApprovalsPage from "./pages/ApprovalsPage";
import ReportsPage from "./pages/ReportsPage";
import AuditPage from "./pages/AuditPage";
import ComparePage from "./pages/ComparePage";
import AskPage from "./pages/AskPage";
import PredictPage from "./pages/PredictPage";
import ExportPage from "./pages/ExportPage";
import MarketingPage from "./pages/MarketingPage";
import AnalyticsPage from "./pages/AnalyticsPage";

export default function App() {
  return (
    <BrowserRouter>
      <Layout>
        <Routes>
          <Route path="/" element={<Navigate to="/sources" replace />} />
          <Route path="/sources" element={<SourcesPage />} />
          <Route path="/approvals" element={<ApprovalsPage />} />
          <Route path="/reports" element={<ReportsPage />} />
          <Route path="/audit" element={<AuditPage />} />
          <Route path="/compare" element={<ComparePage />} />
          <Route path="/ask" element={<AskPage />} />
          <Route path="/predict" element={<PredictPage />} />
          <Route path="/analytics" element={<AnalyticsPage />} />
          <Route path="/marketing" element={<MarketingPage />} />
          <Route path="/export" element={<ExportPage />} />
          {/* Any unrecognised path lands somewhere real. Without this, an
              unknown route rendered the nav and an EMPTY main area - no
              error, no message, just a blank page that looks like the app
              failed to load. A stale bookmark or a hand-edited URL should
              never be indistinguishable from a broken build. */}
          <Route path="*" element={<Navigate to="/sources" replace />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  );
}
