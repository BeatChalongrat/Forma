import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import { LucideProvider } from 'lucide-react'
import '@fontsource-variable/montserrat'
import '@fontsource-variable/noto-sans-thai'
import '@fontsource-variable/jetbrains-mono'
import './index.css'
import { Shell } from './components/Shell'
import { ToastProvider } from './lib/toast'
import { Overview } from './pages/Overview'
import { Imports, NewImport } from './pages/Imports'
import { RoundPage } from './pages/Round'
import { MappingStudio } from './pages/MappingStudio'
import { ReviewCenter } from './pages/Review'
import { Customers, CustomerPage } from './pages/Customers'
import { DataEditor } from './pages/DataEditor'
import { SettingsPage } from './pages/Settings'
import { OpsPage } from './pages/Ops'
import { GuidePage } from './pages/Guide'
import { ArtifactsPage } from './pages/Artifacts'
import { MemoryPage } from './pages/Memory'
import { BulkUpload, UploadPage } from './pages/Upload'
import { AuthCallback } from './pages/AuthCallback'
import { ensureAuthenticated } from './lib/auth'
import { PreferencesProvider } from './lib/preferences'
import { AssistantPage } from './components/Assistant'

const qc = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 5000 } } })

const router = createBrowserRouter([
  {
    path: '/',
    element: <Shell />,
    children: [
      { index: true, element: <Overview /> },
      { path: 'imports', element: <Imports /> },
      { path: 'artifacts', element: <ArtifactsPage /> },
      { path: 'memory', element: <MemoryPage /> },
      { path: 'imports/new', element: <BulkUpload /> },
      { path: 'imports/new/single', element: <NewImport /> },
      { path: 'uploads/:batchId', element: <UploadPage /> },
      { path: 'rounds/:roundId', element: <RoundPage /> },
      { path: 'rounds/:roundId/mapping/:mappingId', element: <MappingStudio /> },
      { path: 'review', element: <ReviewCenter /> },
      { path: 'assistant', element: <AssistantPage /> },
      { path: 'customers', element: <Customers /> },
      { path: 'customers/:customerId', element: <CustomerPage /> },
      { path: 'datasets/:datasetId', element: <DataEditor /> },
      { path: 'settings', element: <SettingsPage /> },
      { path: 'ops', element: <OpsPage /> },
      { path: 'guide', element: <GuidePage /> },
    ],
  },
  { path: '/auth/callback', element: <AuthCallback /> },
])

void ensureAuthenticated().finally(() => {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <QueryClientProvider client={qc}>
        <ToastProvider>
          <PreferencesProvider><LucideProvider strokeWidth={1.5}><RouterProvider router={router} /></LucideProvider></PreferencesProvider>
        </ToastProvider>
      </QueryClientProvider>
    </StrictMode>,
  )
})
