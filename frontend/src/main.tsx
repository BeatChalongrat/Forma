import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'
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

const qc = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 5000 } } })

const router = createBrowserRouter([
  {
    path: '/',
    element: <Shell />,
    children: [
      { index: true, element: <Overview /> },
      { path: 'imports', element: <Imports /> },
      { path: 'imports/new', element: <NewImport /> },
      { path: 'rounds/:roundId', element: <RoundPage /> },
      { path: 'rounds/:roundId/mapping/:mappingId', element: <MappingStudio /> },
      { path: 'review', element: <ReviewCenter /> },
      { path: 'customers', element: <Customers /> },
      { path: 'customers/:customerId', element: <CustomerPage /> },
      { path: 'datasets/:datasetId', element: <DataEditor /> },
      { path: 'settings', element: <SettingsPage /> },
    ],
  },
])

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <RouterProvider router={router} />
      </ToastProvider>
    </QueryClientProvider>
  </StrictMode>,
)
