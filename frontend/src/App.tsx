// Routes exactly as docs/mycelic/API.md "Frontend routes".
import { Navigate, Route, Routes } from 'react-router-dom';
import { RedirectIfAuthed, RequireAuth } from './auth/RequireAuth';
import { AppShell, NotFound } from './shell/AppShell';
import { LoginPage } from './pages/LoginPage';
import { RegisterPage } from './pages/RegisterPage';
import { InvitePage } from './pages/InvitePage';
import { OnboardingPage } from './pages/OnboardingPage';
import { EmployeeHomePage } from './pages/EmployeeHomePage';
import { MemoryPage } from './pages/MemoryPage';
import { ConnectedAppsPage } from './pages/ConnectedAppsPage';
import { ChatPage } from './pages/ChatPage';
import { GoalsPage } from './pages/GoalsPage';
import { GoalDetailPage } from './pages/GoalDetailPage';
import { QuestionDetailPage } from './pages/QuestionDetailPage';
import { DiscoveryDetailPage } from './pages/DiscoveryDetailPage';
import { ClaimDetailPage } from './pages/ClaimDetailPage';
import { UnitWorkspacePage } from './pages/UnitWorkspacePage';
import { ExecutivePage } from './pages/ExecutivePage';
import { NetworkPage } from './pages/NetworkPage';
import { AdminLayout } from './pages/admin/AdminLayout';
import { AdminOverviewPage } from './pages/admin/OverviewPage';
import { MembersPage } from './pages/admin/MembersPage';
import { HierarchyPage } from './pages/admin/HierarchyPage';
import { PoliciesPage } from './pages/admin/PoliciesPage';
import { ModelsPage } from './pages/admin/ModelsPage';
import { BudgetsPage } from './pages/admin/BudgetsPage';
import { IntegrationsPage } from './pages/admin/IntegrationsPage';
import { DomainsPage } from './pages/admin/DomainsPage';
import { WorkersPage } from './pages/admin/WorkersPage';
import { AuditPage } from './pages/admin/AuditPage';
import { DeploymentPage } from './pages/admin/DeploymentPage';

export function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/app" replace />} />
      <Route path="/login" element={<RedirectIfAuthed><LoginPage /></RedirectIfAuthed>} />
      <Route path="/register" element={<RedirectIfAuthed><RegisterPage /></RedirectIfAuthed>} />
      <Route path="/invite/:token" element={<InvitePage />} />
      <Route element={<RequireAuth />}>
        <Route element={<AppShell />}>
          <Route path="/onboarding" element={<OnboardingPage />} />
          <Route path="/app" element={<EmployeeHomePage />} />
          <Route path="/app/memory" element={<MemoryPage />} />
          <Route path="/app/memory/integrations" element={<ConnectedAppsPage />} />
          <Route path="/app/chat" element={<ChatPage />} />
          <Route path="/app/chat/:chatId" element={<ChatPage />} />
          <Route path="/app/goals" element={<GoalsPage />} />
          <Route path="/app/goals/:id" element={<GoalDetailPage />} />
          <Route path="/app/questions/:id" element={<QuestionDetailPage />} />
          <Route path="/app/discoveries/:id" element={<DiscoveryDetailPage />} />
          <Route path="/app/claims/:id" element={<ClaimDetailPage />} />
          <Route path="/app/unit/:unitId" element={<UnitWorkspacePage />} />
          <Route path="/app/executive" element={<ExecutivePage />} />
          <Route path="/app/network" element={<NetworkPage />} />
          <Route path="/app/admin" element={<AdminLayout />}>
            <Route index element={<AdminOverviewPage />} />
            <Route path="members" element={<MembersPage />} />
            <Route path="hierarchy" element={<HierarchyPage />} />
            <Route path="policies" element={<PoliciesPage />} />
            <Route path="models" element={<ModelsPage />} />
            <Route path="budgets" element={<BudgetsPage />} />
            <Route path="integrations" element={<IntegrationsPage />} />
            <Route path="domains" element={<DomainsPage />} />
            <Route path="workers" element={<WorkersPage />} />
            <Route path="audit" element={<AuditPage />} />
            <Route path="deployment" element={<DeploymentPage />} />
          </Route>
          <Route path="*" element={<NotFound />} />
        </Route>
      </Route>
    </Routes>
  );
}
